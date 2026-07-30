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
    """Prefer the record with more non-null fields; tie-break on line-item count."""
    def score(inv: Invoice) -> tuple[int, int]:
        completeness = sum([
            inv.stated_subtotal is not None,
            inv.stated_tax is not None,
            inv.payment_terms is not None,
            inv.vendor_address is not None,
        ])
        return (completeness, len(inv.line_items))

    ranked = sorted(group, key=score, reverse=True)
    return ranked[0], ranked[1:]


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
