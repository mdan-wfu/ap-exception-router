# Evaluation results

Run against `eval/ground_truth.yaml` over the 16-invoice corpus in `LLM_MODE=replay` against the committed cassettes. Zero live API calls. Reproducible via `make eval`.

## Manifest (as of 2026-07-31)

```
model=grok-4.5  mode=replay  cassettes=292  git=e3c293e
threshold=$10,000  near-band=5%  price-tol=±5%  terms-tol=±2d
fx={'EUR': 1.14} (as of 2026-07-28)  human-gate=demo
```

Ground truth follows the Phase 7 reconciliation: outcomes are recorded as sets where two Adjudicator decisions are defensible (INV-1005 / 1008 / 1009 / 1010 / 1013 / 1016), single-valued elsewhere.

## Headline numbers

| Layer | Result | Denominator |
|---|---|---|
| **Extraction accuracy** | 130 / 131 = **99.2%** | field-level checks across all 16 invoices |
| **Must-fire findings** | 41 / 41 = **100%** | codes ground truth says MUST fire, per invoice |
| **Decision agreement** | 16 / 16 = **100%** | Adjudicator outcome ∈ expected-outcome set |

`make eval` exits 0 (PASS) — no MUST-fire misses, no single-valued outcome divergence.

## Extraction accuracy by source format

| Format | Matched | Checked | Rate |
|---|---|---|---|
| CSV  | 24 | 24 | 100.0% |
| JSON | 43 | 43 | 100.0% |
| PDF  | 17 | 18 |  94.4% |
| TXT  | 38 | 38 | 100.0% |
| XML  |  8 |  8 | 100.0% |

The single miss lives on the PDF path — expected given CLAUDE.md §6's characterization of the INV-1011 PDF as "less complete than its txt source."

## Named misses

### Extraction

**INV-1011 / pdf / payment_terms: expected `Net 30`, actual `None`.** The PDF version of INV-1011 omits the "Payment Terms" label that the txt version carries. The alphabetical iteration in `main.py` processes `invoice_1011.pdf` before `invoice_1011.txt` and skips the txt (same invoice number already seen). CLAUDE.md §6 explicitly calls out that dedupe should "prefer the more complete record" — the deduplicator recommends txt as the retained file (via DP-001 evidence), but `main.py`'s loop doesn't currently honor that recommendation. Result: the more-complete txt is discarded and the payment_terms field is lost on this invoice pair. **Named defect, deferred to a future phase** — the fix requires changing the batch iterator to consult DP-001 evidence for the retained-file preference, which changes runtime behavior and would require a live re-record of INV-1011.

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

- Total spend: **$1.12406** across 16 invoices
- Per-invoice mean: **$0.07025**
- Total model calls: 118, distributed adjudicator 34% / critic 31% / adjudicator_revised 30% / scribe 6% of spend
- Cassette-hit wall clock: under 2 s for the full corpus in replay
- Queue depth after demo human gate resolves: 7 HOLD (INV-1004, 1005, 1007, 1008, 1009, 1012, 1014)
- Settled: 5 PAID / 4 REJECTED / 7 QUEUED

## Business extrapolation

*Assumptions:* Acme's stated baseline is 30% error rate, 5-day cycle time, $2M/year loss. Volumes and per-invoice manual-handling time are not given in the brief; the extrapolation below states its own assumptions inline and does not claim precision beyond them.

**Straight-through today.** 4 of 16 invoices (25%) landed as APPROVE with no human touch. On a real corpus with a similar clean/exception ratio, that's a full quarter of AP volume that never reaches the clerk. At even a modest 3 minutes per invoice of "look at, decide, key" clerk time saved, that's meaningful.

**Where the real value sits.** The system's design bet is that clerk time on the 60–70% of invoices that DO require judgment is where the loss actually lives — cycle time, missed duplicates, missed inventory-aggregate overflows (INV-1013's $22.5k mistake would slip past a rushed human eye). Pre-assembling the evidence (vendor lookup, prior-invoice check, arithmetic, aggregate-quantity check, policy citation) and handing the clerk a scribe note reduces per-exception handling time — plausibly by half — without the clerk giving up decision authority.

**A defensible portion of the $2M.** If we accept the assumption that the majority of the $2M loss is cycle-time cost plus missed-defect cost, the system attacks both:
- Cycle time: 25% straight-through eliminates human latency on those. Escalations resolve in "clerk read + decide" time, not "clerk investigate + decide" time.
- Missed defects: the INV-1013 aggregate-stock overrun ($22.5k, correctly REJECTed) and INV-1007 −$110 undercharge (correctly ESCALATEd with signed delta) are exactly the kind of defect the 30% baseline error rate implies is slipping through today.

We do not claim to eliminate the $2M loss. A defensible framing: the architecture targets **the two large drivers** (cycle time and missed-defect cost) with mechanisms measurable against the eval numbers above. Specific dollar recapture would require Acme's actual volume and cost-per-defect data, which the brief does not supply.

## Reproducibility

`make eval` runs the full harness from a clean checkout. The manifest above pins the exact configuration; each rerun writes a `runs/eval-<ts>.json` alongside the terminal output. Two consecutive `make demo` runs produce byte-identical output (Phase 7 determinism check, still passing at md5 `e145ac42a452fa1dd74b75453696a0b9`).
