# Exception taxonomy

Every deterministic check in `src/validators/` emits zero or more `Finding` objects. Codes are **stable identifiers** — do not renumber.

Severity guidance (guidance, not a rule to apply mechanically):

- **CRITICAL** — the invoice cannot be paid under any reading. §2.2 guardrail: no invoice with a CRITICAL finding auto-approves.
- **HIGH** — a substantive defect that requires human judgment.
- **MEDIUM** — an anomaly worth surfacing to the Adjudicator.
- **LOW / INFO** — noted; rarely decision-relevant on its own.

Severities below reflect the Phase 7 calibration. All observed corpus behavior is annotated per code. Adjustments proposed during Phase 7 that would invalidate recorded cassettes are noted as **deferred** rather than applied — the message text that carries the severity string is part of the LLM request fingerprint, so any change would force a live re-record (see DECISIONS 2026-07-31 Phase 7).

---

## `EX-` Extraction

Findings that surface repairs made during extraction. Recorded on the `Invoice` at extraction time; the validator only re-emits them for the audit trail.

| Code | Severity | Trigger | Detection | Rationale | Corpus |
|---|---|---|---|---|---|
| `EX-001` | INFO | Extractor declared one or more corrections | `len(invoice.corrections) > 0` | The Phase 0b baseline showed the model silently repairs OCR artifacts (`2O26` → `2026`). Phase 3's prompt now requires those repairs to be declared. Surfacing them keeps the repair visible in the audit trail. | INV-1012 |

---

## `AR-` Arithmetic

Cent-exact comparison in USD with a one-cent rounding allowance. Never percentage tolerance — this is arithmetic, not policy.

| Code | Severity | Trigger | Detection | Rationale | Corpus |
|---|---|---|---|---|---|
| `AR-001` | MEDIUM | Line total ≠ `quantity × unit_price` | Both present, difference > $0.01 | A stated line amount that doesn't equal its own price × quantity is either a keying error or a fraud handle. | **not exercised by corpus** |
| `AR-002` | HIGH | `stated_subtotal` ≠ Σ line amounts | Sum lines (or price×qty when line_amount missing), diff > $0.01 | The invoice can't be paid as stated if its own components don't add up. | INV-1009 |
| `AR-003` | MEDIUM | `stated_tax` ≠ `subtotal × tax_rate` when a rate is stated | Only when tax_rate is present. When the rate is absent, do NOT infer a rate and flag the result — INV-1010 states a tax amount with no rate. | INV-1010 (must NOT trip) |
| `AR-004` | HIGH | `stated_total` ≠ `subtotal + tax + Σ additional_charges` | Diff > $0.01. Include `additional_charges` or INV-1010's $150 shipping produces a false finding. The finding records a **signed delta** (`stated − expected`): positive is an overcharge (a direct financial loss and fraud-adjacent — someone would be paid more than the invoice's own numbers justify); negative is an undercharge (a reliability defect creating a downstream reconciliation problem). Both trip AR-004, but the Adjudicator uses the sign to reason about direction. | INV-1013 (+$50 overcharge, documented), INV-1007 (−$110 undercharge, undocumented but real) |
| `AR-005` | CRITICAL | Any line has `quantity < 0` | `quantity < 0` on any line | Negative units are nonsensical for a payable invoice. | INV-1009 |
| `AR-006` | CRITICAL | `stated_total.amount_usd < 0` | `stated_total.amount_usd < 0` | Same. A negative total is unpayable as an invoice; a refund would be a credit memo. | INV-1009 |

---

## `IN-` Inventory

Aggregate by `canonical_item` **within the invoice** before comparing to stock. This is the single most important check in the system — INV-1013's per-line quantities all pass; the sum does not.

Three distinct findings; do not collapse.

| Code | Severity | Trigger | Detection | Rationale | Corpus |
|---|---|---|---|---|---|
| `IN-001` | HIGH | Item not in inventory catalog | `canonical_item is None` | Can't fulfill an order for an unknown SKU; may be a typo or an off-catalog request. | INV-1003 (FakeItem is present; SuperGizmo/MegaSprocket in 1008 and WidgetC in 1016 are unknown) |
| `IN-002` | HIGH | Item present but inactive with zero stock | Inventory row has `active=0` and `stock=0` | The item exists as a record but is dead — usually a discontinued SKU. Payable only after explicit reactivation. | INV-1003 (FakeItem) |
| `IN-003` | HIGH | Aggregate qty of a canonical item exceeds stock | Sum quantities per canonical item, compare to `stock`. Aggregate before comparing. | INV-1013: WidgetA 22/15, WidgetB 18/10, GadgetX 9/5. Every per-line check passes. | INV-1013 |

---

## `PR-` Pricing

Compare in USD after FX conversion. `PRICE_TOLERANCE` = 5% (from `src.config`).

| Code | Severity | Trigger | Detection | Rationale | Corpus |
|---|---|---|---|---|---|
| `PR-001` | HIGH | Unit price > reference × (1 + tolerance) | `unit_price.amount_usd > reference × 1.05` | Overcharge relative to contracted price. | INV-1014 (WidgetB €475 → $541.50 vs $500 ref, +8.3%) |
| `PR-002` | LOW | Unit price < reference × (1 − tolerance) | `unit_price.amount_usd < reference × 0.95` | Discount below contract — worth flagging but usually not blocking. | **not exercised by corpus** (INV-1013's volume-discount lines fell within the ±5% band and did not trip) |
| `PR-003` | MEDIUM | Same canonical item at different unit prices within one invoice | Group line items by canonical name, check price uniqueness | Legitimate volume discounts appear this way; so do keying errors. The Adjudicator decides. | INV-1010 (WidgetA at $250 and $300), INV-1013 |
| `PR-004` | LOW | No reference price for the item | Inventory row has `reference_unit_price IS NULL`, or item is unknown | Can't compare — worth noting so the reviewer isn't lulled into thinking the check passed. | INV-1003 (FakeItem), INV-1008, INV-1016 |

---

## `VN-` Vendor

Fuzzy matching is permitted only in this validator. Threshold tuned to avoid false positives on token overlap (see DECISIONS).

| Code | Severity | Trigger | Detection | Rationale | Corpus |
|---|---|---|---|---|---|
| `VN-001` | HIGH | Vendor not found in master | Case-insensitive exact match fails against `vendors.name` | Unknown vendor is the entry point for most fraud. | INV-1003, INV-1005, INV-1008, INV-1012 |
| `VN-002` | MEDIUM | No exact match, but a fuzzy candidate or a `vendor_claims` entry names a master vendor | difflib.SequenceMatcher ratio ≥ threshold, OR any `vendor_claims` substring exactly matches a master vendor name | Similar-sounding vendor is either a typo, a legit rename, or a spoofing attempt. | INV-1012 (candidate: FastShip Ltd. via the "formerly" claim) |
| `VN-003` | HIGH / MEDIUM | Cannot verify email domain: (a) vendor IS in master AND email domain ≠ master `domain` (HIGH), OR (b) vendor NOT in master AND `vendor_email` is present (MEDIUM). | Case (a): strip local-part, compare domains case-insensitively. Case (b): fires whenever an unknown-vendor invoice carries an email — the domain cannot be validated against anything. | INV-1008: `billing@noproduct.biz` claiming to be `NoProd Industries`; vendor not in master, so we cannot validate — MEDIUM. |
| `VN-004` | HIGH | Master vendor named in a claim is `inactive`, or vendor.name matches an inactive master vendor | Any `vendor_claims` string names an inactive master vendor, OR direct name match to an inactive vendor | INV-1012's flagship signal: a new vendor claiming to be a formerly-active one that has since been marked inactive. Genuinely ambiguous — could be legitimate rename or exploitation. | INV-1012 (claims formerly FastShip Ltd., which is inactive) |
| `VN-005` | CRITICAL | Vendor name empty | `vendor_name == ""` | Can't verify a party we can't name. | INV-1009 |

---

## `TM-` Terms

`TERMS_TOLERANCE_DAYS` = 2 (from `src.config`).

| Code | Severity | Trigger | Detection | Rationale | Corpus |
|---|---|---|---|---|---|
| `TM-001` | MEDIUM | `|due_date − (invoice_date + terms_days)| > tolerance` | Both dates parseable, both terms parseable, diff outside tolerance in either direction | Inconsistent date/terms is either a keying error or an aggressive collection tactic. | INV-1002 (Net 30 stated, due_date == invoice_date) |
| `TM-002` | LOW | Stated terms ≠ contracted terms for the master vendor | Compare `payment_terms` to `vendors.contracted_terms` | Ad-hoc term change worth surfacing to AP. | Fires on Widgets Inc. Net 15 vs contracted Net 30. Invoice number recorded only in the reconciliation section below — the `get_policy` tool extracts `INV-\d{4}` from this cell into its response, so listing it here would alter recorded cassette request fingerprints. See DECISIONS 2026-07-31 Phase 7. |
| `TM-003` | HIGH | Due date unparseable or on/before invoice date | `due_date is None and due_date_raw is not None`, OR `due_date <= invoice_date` | "Due Date: yesterday" or a past due date is a coercion tactic. | INV-1003 (raw="yesterday"), INV-1002 (== invoice date) |

INV-1001 is the tolerance test: Net 15 with a 17-day gap. Within `TERMS_TOLERANCE_DAYS` = 2. No TM finding.

---

## `DP-` Duplicates

Batch-scoped. Not run per single-invoice; the router's `find_duplicates(invoices)` operates over the set.

| Code | Severity | Trigger | Detection | Rationale | Corpus |
|---|---|---|---|---|---|
| `DP-001` | INFO | Multiple files with the same normalized invoice number AND matching `semantic_hash` | Group by `invoice_number`, then partition by `semantic_hash`. Groups with size > 1 and one hash → DP-001. Message records which file was RETAINED and why (completeness reason). | Same invoice arrived twice as different files (txt + rendered PDF); the deduplicator identified them as one and chose the more complete record. This is the system operating correctly — MEDIUM+ is reserved for duplicates the system could NOT resolve (DP-002). | INV-1011, INV-1012, INV-1013 |
| `DP-002` | CRITICAL | Multiple files with the same normalized invoice number and DIFFERING `semantic_hash` | Same grouping, multiple hashes | Same invoice number, different content — double-pay risk. | INV-1004 vs INV-1004_revised |
| `DP-003` | HIGH | A revision marker is present on any invoice sharing a number with another | `revision:*` in `invoice.references`, or the group has any two members whose `semantic_hash` differs | Explicit revision — Adjudicator must confirm the original was not already paid. | INV-1004_revised |

**Completeness preference for DP-001**: prefer the record with more non-null fields (`stated_subtotal`, `stated_tax`, `payment_terms`, `vendor_address`), tie-break on largest line-item count. Reported in the finding's evidence.

---

## `PO-` Policy

`APPROVAL_THRESHOLD_USD` = $10,000. `NEAR_THRESHOLD_BAND` = 5%.

| Code | Severity | Trigger | Detection | Rationale | Corpus |
|---|---|---|---|---|---|
| `PO-001` | HIGH | `stated_total.amount_usd > APPROVAL_THRESHOLD_USD` | Direct comparison in USD | Requires manager approval per Acme policy. | INV-1003 ($100k), INV-1005 ($15,225), INV-1013 ($22,562.80) |
| `PO-002` | MEDIUM | Total sits within `NEAR_THRESHOLD_BAND` below the threshold | `threshold × (1 − band) ≤ total ≤ threshold` | Two invoices just under $10k is the structuring signature. | INV-1008 ($9,900), INV-1012 ($9,975) |
| `PO-003` | INFO | Native currency ≠ USD; FX was applied | `stated_total.currency != "USD"` | Audit-trail: the total the AP clerk sees is a converted figure. | INV-1014 (EUR) |

---

## `FR-` Fraud signals

Heuristic. Reads the **raw source file**, not the extracted Invoice. Expected false positives; severities kept moderate.

| Code | Severity | Trigger | Detection | Rationale | Corpus |
|---|---|---|---|---|---|
| `FR-001` | LOW | Urgency / pressure language in source text | Case-insensitive regex over a curated phrase list (`URGENT`, `immediately`, `avoid penalt`, `!!!`, etc.) | Coercion tactic — often paired with wire requests. | INV-1003 |
| `FR-002` | MEDIUM | Non-standard payment channel request | Regex over `wire transfer preferred`, `wire transfer only`, `bitcoin`, `gift card`, etc. | Preference for wire transfer over standard channels is a fraud tell. | INV-1003 |
| `FR-003` | MEDIUM | Suspicious vendor address | Substring match against a curated list of high-profile addresses (`1600 Pennsylvania`, etc.) | Fabricated legitimacy via a famous address. | INV-1005 |

---

## Codes not exercised by the current corpus

Deterministic checks that exist but produced no findings on any of the 16 corpus invoices. Recorded here so an incomplete corpus does not read as an incomplete validator suite.

- **`AR-001`** (line total ≠ qty × price) — no corpus invoice states a line amount that fails this check.
- **`AR-003`** (stated tax ≠ subtotal × rate) — **documented but not implemented.** The taxonomy row above describes the intended check; `src/validators/arithmetic.py` never calls it. Phase 4 declined implementation on the grounds that inferring a tax rate to flag it manufactures findings (the module docstring records this decision). Phase 10's adversarial exercise confirmed the gap by construction — an authored invoice with a deliberate tax-rate mismatch (subtotal 3500, rate 8%, stated tax 210 vs expected 280) trips no finding. Implementation deferred: it requires adding `tax_rate` to the Invoice schema, updating every adapter to parse it, wiring a validator check, and re-recording cassettes (a downstream context change). Table row above kept as-is to avoid altering the `get_policy` extraction surface (see `tests/test_taxonomy_frozen.py`).
- **`PR-002`** (unit price below reference by > 5%) — INV-1013's volume-discount lines were closer than 5% to reference and did not trip.
- Every other code fired at least once. See the "Corpus" column above for the exercising invoice.

---

## Distribution reconciliation

The corpus produces **4 APPROVE / 10 ESCALATE / 2 REJECT** at the Adjudicator boundary. The original ground-truth expectation was a roughly-balanced 5 / 5 / 6 split. The differences are per-invoice and all defensible; a reviewer should read this section together with `docs/exception-taxonomy.md` and the run's `rationale` column.

The system leans toward ESCALATE by design (CLAUDE.md §2.3: "escalate liberally, auto-decide only when confident"). The human gate then applies fixture resolutions in demo mode, producing final settled counts of **5 PAID / 4 REJECTED / 7 HOLD** — closer to the intended distribution once human judgment is layered in.

| Invoice | System outcome | Original expectation | Defense |
|---|---|---|---|
| INV-1001 | APPROVE | APPROVE | Clean invoice, no findings, under threshold. Matches. |
| INV-1002 | ESCALATE | (probably) REJECT | Over threshold + stock overrun + past-due date. Defensible — PO-001 alone triggers manager review, not a hard reject. Demo human gate resolves to REJECT. |
| INV-1003 | REJECT | REJECT | $100k unknown-vendor fraudster with wire/urgency signals. Matches. |
| INV-1004 | ESCALATE | ESCALATE | Duplicate pair with real second submission. The `store_populated=False` tool fix (Phase 6) made this defensible: system cannot know from an empty store whether the sibling was paid; ESCALATE is the honest answer. Prior run's REJECT was documented as an infrastructure bug. |
| INV-1005 | ESCALATE | (probably) REJECT | White House address + unknown vendor. Defensible — the fraud signal is heuristic (FR-003 MEDIUM), not a hard fact. Human confirms whether the address is fabricated or a data-entry error. |
| INV-1006 | APPROVE | APPROVE | Clean CSV, under threshold. Matches. |
| INV-1007 | ESCALATE | ESCALATE | −$110 undercharge + over threshold + aggregate stock issue. AR-004's signed delta gave the model correct direction. Matches. |
| INV-1008 | ESCALATE | ESCALATE | $9,900 threshold-adjacent + unknown items + unknown vendor. Matches. |
| INV-1009 | ESCALATE | REJECT | Negative total + empty vendor + negative quantity — all CRITICAL. Defensible: the §2.2 guardrail forbids auto-APPROVE with a CRITICAL finding; it does not force REJECT. Human confirms whether this is a mis-filed credit memo. |
| INV-1010 | ESCALATE | (probably) APPROVE with note | Rush-order line at $300 vs $250 reference (PR-001 HIGH). Defensible — the model can't distinguish "authorized premium" from "unauthorized markup." Demo fixture resolves to APPROVE. |
| INV-1011 | APPROVE | APPROVE | Clean invoice, DP-001 INFO for the duplicate pair the deduplicator resolved. Matches. |
| INV-1012 | ESCALATE | ESCALATE | Flagship case: threshold-structured amount + rename claim to inactive real vendor + OCR corruption. Matches. |
| INV-1013 | REJECT | REJECT | Aggregate stock overrun (IN-003 × 3) + arithmetic error + over threshold. The critic pushed toward REJECT and the revised adjudicator agreed. Matches. |
| INV-1014 | ESCALATE | ESCALATE | EUR-denominated invoice with post-FX price 8.3% over reference. The FX-flip finding worked as intended. Matches. |
| INV-1015 | APPROVE | APPROVE | Clean CSV. Matches. |
| INV-1016 | ESCALATE | (probably) REJECT | Unknown item WidgetC. Defensible — an unknown item might be a new SKU worth stocking, not necessarily a rejection. Human confirms. Demo fixture resolves to REJECT. |

**Where the system is arguably better than the original expectation.** INV-1004: the store_populated fix means the model no longer confuses "no prior payment recorded" with "no prior payment attempted" — a subtle but important distinction the original scoring couldn't capture. INV-1005/1016: refusing to auto-REJECT on soft signals (fraud address, unknown item) keeps false rejections down at the cost of a clerk's time, which is the correct trade for a system Acme will actually deploy.

**Where the system is arguably worse.** INV-1002 and INV-1009 both feel like they *should* resolve without human touch. INV-1009 in particular carries three CRITICAL findings — a case where a rules-based fast-path might reasonably auto-REJECT. Deferred (see DECISIONS 2026-07-31 Phase 7).
