# Evaluation results

Run against `eval/ground_truth.yaml` over the 16-invoice corpus in `LLM_MODE=replay` against the committed cassettes. Zero live API calls. Reproducible via `make eval`.

## Manifest (as of 2026-07-31)

```
model=grok-4.5  mode=replay  cassettes=295  git=post-duplicate-selection-fix
threshold=$10,000  near-band=5%  price-tol=±5%  terms-tol=±2d
fx={'EUR': 1.14} (as of 2026-07-28)  human-gate=demo
```

Ground truth follows the Phase 7 reconciliation: outcomes are recorded as sets where two Adjudicator decisions are defensible (INV-1005 / 1008 / 1009 / 1010 / 1013 / 1016), single-valued elsewhere.

## Headline numbers

| Layer | Result | Denominator |
|---|---|---|
| **Extraction accuracy** | 131 / 131 = **100%** | field-level checks across all 16 invoices |
| **Must-fire findings** | 41 / 41 = **100%** | codes ground truth says MUST fire, per invoice |
| **Decision agreement** | 16 / 16 = **100%** | Adjudicator outcome ∈ expected-outcome set |

`make eval` exits 0 (PASS) — no MUST-fire misses, no single-valued outcome divergence, no extraction misses.

## Extraction accuracy by source format

| Format | Matched | Checked | Rate |
|---|---|---|---|
| CSV  | 24 | 24 | 100.0% |
| JSON | 43 | 43 | 100.0% |
| PDF  | 10 | 10 | 100.0% |
| TXT  | 46 | 46 | 100.0% |
| XML  |  8 |  8 | 100.0% |

## Named misses

### Extraction

None. The one extraction miss the eval originally surfaced (INV-1011 payment_terms) was fixed in code and is documented below under "The duplicate-selection fix — story of a near-miss."

### Findings

None. Every ground-truth `must_fire` code was raised. Unexpected findings (`may_fire` and unlisted codes) were reviewed and are legitimate:
- INV-1002 fires TM-001 in addition to TM-003 (both correct — Net 30 stated with due_date=invoice_date trips both `due<=invoice` and `|due−(invoice+terms)| > tolerance`).
- INV-1003, 1008, 1016 fire PR-004 alongside their must-fires — this is `may_fire` per ground truth (unknown items have no reference price, so PR-004 fires by design).
- INV-1005 fires FR-003 (White House address) — expected `may_fire`.

### Decisions

None. All 16 outcomes fall within their expected-outcome sets.

## Ground-truth correction made during this run

**INV-1009: added `AR-004` to `must_fire`.** The first draft only listed AR-002 (subtotal ≠ Σ line amounts). AR-004 (stated_total ≠ subtotal + tax + additional_charges) also fires — subtotal=1000 stated, tax=0, additional_charges=[], expected total 1000, actual stated -250, delta -1250. Legitimate, deterministic finding. Not a system defect; a ground-truth omission. Recorded in DECISIONS 2026-07-31 Phase 9.

No other ground-truth changes were made to preserve any score.

## Corpus behavior in cost / latency

From the same run's audit store (via `make report`):

- Total spend: **$1.12291** across 16 invoices
- Per-invoice mean: **$0.07018**
- Total model calls: 118, distributed adjudicator 34% / critic 31% / adjudicator_revised 30% / scribe 6% of spend
- Cassette-hit wall clock: under 2 s for the full corpus in replay
- Queue depth after demo human gate resolves: 7 HOLD (INV-1004, 1005, 1007, 1008, 1009, 1012, 1014)
- Settled: 5 PAID / 4 REJECTED / 7 QUEUED

## The duplicate-selection fix — story of a near-miss

Worth telling straight, because the near-miss is part of the story.

**What eval surfaced.** INV-1011 arrived as a TXT/PDF pair. The batch loop processed the PDF (alphabetically first) and skipped the TXT. The PDF is genuinely less complete than its TXT counterpart (CLAUDE.md §6). Result: `payment_terms=Net 30` present in TXT was lost. One extraction field, on one invoice.

**Root cause.** `find_duplicates` already computes which member of a DP-001 group to retain (completeness score, tie-break on line-item count, then alphabetical basename). The batch loop ignored that computation and used sorted-order-of-input instead. Fixable in one place.

**The first fix attempt was itself wrong.** Wiring `pick_retained` unconditionally over every `by_invoice_number` group would collapse DP-002 pairs too — and DP-002 pairs are precisely the case where the two files are GENUINELY DIFFERENT submissions (INV-1004's original vs revised). `pick_retained`'s tie-break on line-item count would have swapped INV-1004's original for its revised version because the revision has one more line item. That is a category error: the whole point of DP-002 is that a human must decide which submission is authoritative — the batch loop must never make that call.

**Caught before commit** by the md5 stop-rule on `make demo`: the first-attempt fix changed the demo output, and INV-1004's adjudicate call missed on cassette. The stop-rule was in the task prompt specifically to catch this class of surprise. Nothing was committed while the wrong fix was live.

**Corrected with semantic-hash scoping.** The final fix (`select_batch_retentions`) groups by invoice_number then subgroups by semantic_hash:
- singleton group → keep it
- matching-hash group (DP-001, same invoice, multiple files) → `pick_retained` applies
- differing-hash group (DP-002, different submissions) → alphabetical-first, no completeness scoring

Two tests specifically lock the DP-002 behavior: INV-1004 must select `invoice_1004.json` (alphabetical), NOT tie-break on line-items to `invoice_1004_revised.json`. INV-1011 must select `invoice_1011.txt` (DP-001 completeness winner).

**INV-1011 re-recorded live.** With the scoped fix in place, only INV-1011's adjudicator/critic/scribe chain missed cache — every other invoice replayed correctly. One `LLM_MODE=auto` batch recorded INV-1011's new cassettes. Actual live-API cost of the re-record: **$0.01944** (3 model calls, 1 tool call — a clean-invoice fast path, since the TXT extraction is complete and the invoice under threshold with no findings).

**Result:** extraction accuracy moves from 130/131 (99.2%) to 131/131 (100%). Every other number unchanged. `make demo` byte-identical across consecutive runs at md5 `d31895b6b7320e729324b4e56d93a4f8`.

## Business extrapolation

*Assumptions:* Acme's stated baseline is 30% error rate, 5-day cycle time, $2M/year loss. Volumes and per-invoice manual-handling time are not given in the brief; the extrapolation below states its own assumptions inline and does not claim precision beyond them.

**Straight-through today.** 4 of 16 invoices (25%) landed as APPROVE with no human touch. On a real corpus with a similar clean/exception ratio, that's a full quarter of AP volume that never reaches the clerk. At even a modest 3 minutes per invoice of "look at, decide, key" clerk time saved, that's meaningful.

**Where the real value sits.** The system's design bet is that clerk time on the 60–70% of invoices that DO require judgment is where the loss actually lives — cycle time, missed duplicates, missed inventory-aggregate overflows (INV-1013's $22.5k mistake would slip past a rushed human eye). Pre-assembling the evidence (vendor lookup, prior-invoice check, arithmetic, aggregate-quantity check, policy citation) and handing the clerk a scribe note reduces per-exception handling time — plausibly by half — without the clerk giving up decision authority.

**A defensible portion of the $2M.** If we accept the assumption that the majority of the $2M loss is cycle-time cost plus missed-defect cost, the system attacks both:
- Cycle time: 25% straight-through eliminates human latency on those. Escalations resolve in "clerk read + decide" time, not "clerk investigate + decide" time.
- Missed defects: the INV-1013 aggregate-stock overrun ($22.5k, correctly REJECTed) and INV-1007 −$110 undercharge (correctly ESCALATEd with signed delta) are exactly the kind of defect the 30% baseline error rate implies is slipping through today.

We do not claim to eliminate the $2M loss. A defensible framing: the architecture targets **the two large drivers** (cycle time and missed-defect cost) with mechanisms measurable against the eval numbers above. Specific dollar recapture would require Acme's actual volume and cost-per-defect data, which the brief does not supply.

## Reproducibility

`make eval` runs the full harness from a clean checkout. The manifest above pins the exact configuration; each rerun writes a `runs/eval-<ts>.json` alongside the terminal output. Two consecutive `make demo` runs produce byte-identical output at md5 `d31895b6b7320e729324b4e56d93a4f8` (Phase 7 determinism check, updated to reflect the INV-1011 re-record).

---

## Authored adversarial set (Phase 10)

Four hand-authored invoices in `data/adversarial/`, kept in a **separate corpus, separate ground truth, separate eval target**. Never blended into the provided-corpus numbers above. Target: the three finding codes the Phase 7 audit flagged as "not exercised by corpus" (AR-001, AR-003, PR-002), plus a threshold-structuring pair.

Run: `make eval-adversarial`.

### Headline

| Layer | Result | Denominator |
|---|---|---|
| **Extraction accuracy** | 32 / 32 = **100%** | field-level checks |
| **Must-fire findings** | 4 / 4 = **100%** | codes ground truth requires |
| **Decision agreement** | 4 / 4 = **100%** | outcome ∈ expected set |

Live spend to record: **$0.09781** for the four-invoice batch (budget was $0.50). Extractor cassettes and agent-loop cassettes committed to `data/cassettes/`; the provided corpus's cassettes untouched.

### Per invoice

| Invoice | Target | Fired | Outcome | Expected | Notes |
|---|---|---|---|---|---|
| INV-2001 (txt, Widgets Inc.) | AR-001 | AR-001 + PO-002 fired | ESCALATE | ESCALATE | Line 1 states $2,250 for 8 × $250 (expected $2,000); total lands in the 5% near-threshold band → PO-002 co-fires. |
| INV-2002 (json, Precision Parts) | AR-003 | (none fired) | APPROVE | APPROVE | **AR-003 is documented in the taxonomy but not implemented** in `src/validators/arithmetic.py` — the module docstring calls this out explicitly ("we do not have tax_rate on the schema"). Authoring an invoice targeting AR-003 was the only way to prove the gap by observation. Ground truth reflects the current implementation, not the taxonomy. |
| INV-2003 (csv, Acme Industrial) | PR-002 | PR-002 fired (LOW) | APPROVE | APPROVE | WidgetA at $180 is 28% below the $250 reference (tolerance ±5%). Single LOW-severity finding — Adjudicator correctly does not escalate on a favorable-looking discount alone. |
| INV-2004 (txt, Widgets Inc.) | PO-002 + pair with 2001 | PO-002 fired | ESCALATE | APPROVE / ESCALATE | Individually clean, sits in the 5% near-threshold band. **Threshold-structuring result:** the invoice fires PO-002 on its own, and INV-2001 fires PO-002 on its own, but there is **no cross-invoice aggregator** that recognizes "two invoices from the same vendor, three days apart, each sub-threshold" as a joint signal. The current pipeline processes invoices independently; pair detection was on the Phase 7 cut list and remains cut. Honest result: the individual flags fire, the joint pattern goes undetected. |

### What the adversarial exercise surfaced

- **AR-003 is dead code.** Documented in `docs/exception-taxonomy.md`, has no implementation in the arithmetic validator, and no corpus (provided or authored) can make it fire. The taxonomy's "not exercised by corpus" annotation is polite; the truer statement is "not implemented." Recorded in DECISIONS.
- **PR-002 works and is calibrated correctly at LOW.** A −28% discount on an otherwise-clean single-line invoice correctly routes to APPROVE — the model doesn't over-trust the favorable-looking anomaly nor over-react to a single LOW-severity finding. Encouraging.
- **AR-001 works and routes sensibly.** A single arithmetic defect on an otherwise-plausible invoice trips MEDIUM, which combined with the near-threshold co-firing of PO-002 lands ESCALATE — the expected treatment.
- **Threshold structuring is per-invoice-only.** The eval documents the gap; a cross-invoice detector is a Phase 11+ feature, not a defect of the current build.

### Taxonomy annotations

The Phase 7 "not exercised by corpus" annotations on AR-001, AR-003, and PR-002 remain in `docs/exception-taxonomy.md`. That column is inside the `get_policy` extraction surface guarded by `tests/test_taxonomy_frozen.py` — updating it would change tool-visible content and invalidate recorded provided-corpus cassettes. The "exercised by authored adversarial set" note lives here in `eval-results.md` instead, which is not tool-visible.
