"""Duplicates validator — batch-scoped.

Distinct signature: needs the SET of invoices to group by normalized
invoice_number. Not a per-invoice check.

Grouping:
  - By normalized invoice_number
  - Within a group, partition by semantic_hash
  - Same number + one hash    -> DP-001 (same invoice, multiple files)
  - Same number + multiple    -> DP-002 (differing content) + DP-003 if
                                  any member carries a revision marker
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from src.schema import Finding, Invoice, Severity


def find(invoices: list[Invoice]) -> list[tuple[Invoice, Finding]]:
    by_number: dict[str, list[Invoice]] = defaultdict(list)
    for inv in invoices:
        by_number[inv.invoice_number].append(inv)

    out: list[tuple[Invoice, Finding]] = []
    for number, group in by_number.items():
        if len(group) < 2:
            continue

        hashes = {inv.semantic_hash for inv in group}
        if len(hashes) == 1:
            # DP-001 — same invoice, multiple files. INFO severity: this is
            # the deduplicator operating correctly, not an exception requiring
            # human attention. MEDIUM+ is reserved for duplicates the system
            # could not resolve (DP-002).
            chosen, _others = _pick_most_complete(group)
            reason = _completeness_reason(chosen, group)
            for inv in group:
                if inv is chosen:
                    msg = (
                        f"{number}: {len(group)} files with matching "
                        f"semantic_hash — RETAINED this file ({reason})"
                    )
                else:
                    msg = (
                        f"{number}: {len(group)} files with matching "
                        f"semantic_hash — dropped; retained "
                        f"{Path(chosen.source_file).name} ({reason})"
                    )
                out.append((inv, Finding(
                    code="DP-001",
                    severity=Severity.INFO,
                    message=msg,
                    evidence=(
                        f"group_files={[i.source_file for i in group]}, "
                        f"retained={chosen.source_file}, reason={reason}"
                    ),
                    field_path="source_file",
                )))
        else:
            # DP-002 — same number, differing content
            for inv in group:
                out.append((inv, Finding(
                    code="DP-002",
                    severity=Severity.CRITICAL,
                    message=(
                        f"{number}: {len(group)} files with DIFFERING semantic_hash — "
                        f"double-payment risk"
                    ),
                    evidence=(
                        f"group_files={[i.source_file for i in group]}, "
                        f"hashes={sorted(hashes)}"
                    ),
                    field_path="semantic_hash",
                )))

        # DP-003 — revision marker anywhere in the group
        for inv in group:
            has_revision = any(r.startswith("revision:") for r in inv.references)
            if has_revision:
                out.append((inv, Finding(
                    code="DP-003",
                    severity=Severity.HIGH,
                    message=f"{number}: revision marker present in this file",
                    evidence=f"references={inv.references}",
                    field_path="references",
                )))

    return out


def _pick_most_complete(group: list[Invoice]) -> tuple[Invoice, list[Invoice]]:
    """Prefer the record with more non-null fields; tie-break on line-item count,
    then on source_file basename alphabetically so the result is stable across
    input orderings (critical: the batch orchestrator relies on this to be
    deterministic regardless of which file it read first)."""
    def score(inv: Invoice) -> tuple[int, int, str]:
        completeness = sum([
            inv.stated_subtotal is not None,
            inv.stated_tax is not None,
            inv.payment_terms is not None,
            inv.vendor_address is not None,
        ])
        # Negate the basename comparator so higher-completeness AND earlier
        # basename both sort "greater" under reverse=True. Store the negated
        # form as a string trick — Python can't negate strs, so wrap in a
        # comparator via ordering: emit basename as the third field and rely
        # on reverse=True flipping "later alphabetical" into "greater",
        # then reverse the alphabetical fallback below.
        return (completeness, len(inv.line_items), Path(inv.source_file).name)

    # Primary: completeness DESC, line_items DESC — use reverse=True.
    # But the alphabetical tie-break must go ASCENDING (a < b picks a).
    # Two-phase sort: sort alphabetically ascending first (stable),
    # then sort by (completeness, line_items) DESC (stable). Stable sort
    # preserves the alphabetical order among equal-completeness entries.
    by_name = sorted(group, key=lambda i: Path(i.source_file).name)
    ranked = sorted(
        by_name,
        key=lambda i: (
            sum([
                i.stated_subtotal is not None,
                i.stated_tax is not None,
                i.payment_terms is not None,
                i.vendor_address is not None,
            ]),
            len(i.line_items),
        ),
        reverse=True,
    )
    return ranked[0], ranked[1:]


def pick_retained(group: list[Invoice]) -> Invoice:
    """Return the invoice to keep from a DP-001-style group (all members
    share a semantic_hash — same invoice, multiple files). Uses the
    completeness rule with a stable alphabetical basename tie-break.

    NOT valid for DP-002 groups (differing semantic_hash — genuinely
    different submissions under the same number). Use
    `select_batch_retentions` at the orchestrator boundary instead of
    calling `pick_retained` directly on mixed-hash groups."""
    if len(group) == 1:
        return group[0]
    chosen, _ = _pick_most_complete(group)
    return chosen


def select_batch_retentions(invoices: list[Invoice]) -> set[str]:
    """Return the set of `source_file` values a batch orchestrator should
    actually process. Encodes the collapse rule for duplicate groups:

      - singleton group (no duplicate): keep it
      - matching-semantic-hash group (DP-001): keep the most-complete member
        via `pick_retained`
      - differing-semantic-hash group (DP-002): keep the alphabetically-first
        source_file. The batch loop must NEVER auto-pick between genuinely
        different submissions — that is the human gate's decision. The
        alphabetical choice is deliberately dumb; it preserves whichever
        submission was first-cataloged and defers the "which is authoritative"
        question to the Adjudicator's DP-002 finding + human resolution.

    The near-miss this scoping prevents: an earlier version of this fix used
    `pick_retained` unconditionally and would have swapped INV-1004's
    original for its revised submission because the revision has more line
    items. See DECISIONS 2026-07-31 duplicate-selection fix."""
    from collections import defaultdict as _dd
    by_number: dict[str, list[Invoice]] = _dd(list)
    for inv in invoices:
        by_number[inv.invoice_number].append(inv)

    retained: set[str] = set()
    for group in by_number.values():
        if len(group) == 1:
            retained.add(group[0].source_file)
            continue
        hashes = {inv.semantic_hash for inv in group}
        if len(hashes) == 1:
            retained.add(pick_retained(group).source_file)
        else:
            # DP-002 semantics: keep first alphabetically, do not collapse.
            first = sorted(group, key=lambda i: Path(i.source_file).name)[0]
            retained.add(first.source_file)
    return retained


def _completeness_reason(chosen: Invoice, group: list[Invoice]) -> str:
    """Human-readable explanation of why `chosen` was retained."""
    chosen_present = [
        name for name, present in [
            ("stated_subtotal", chosen.stated_subtotal is not None),
            ("stated_tax", chosen.stated_tax is not None),
            ("payment_terms", chosen.payment_terms is not None),
            ("vendor_address", chosen.vendor_address is not None),
        ] if present
    ]
    others_lines = [len(i.line_items) for i in group if i is not chosen]
    if others_lines and len(chosen.line_items) > max(others_lines):
        return (
            f"more line items ({len(chosen.line_items)} vs "
            f"{max(others_lines)}); fields present: "
            f"{', '.join(chosen_present) or 'none'}"
        )
    return (
        f"most complete fields: {', '.join(chosen_present) or 'none present'}"
    )
