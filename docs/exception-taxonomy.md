# Exception taxonomy

Every deterministic check in `src/validators/` emits zero or more `Finding` objects. Codes are **stable identifiers** — do not renumber.

Severity guidance (guidance, not a rule to apply mechanically):

- **CRITICAL** — the invoice cannot be paid under any reading. §2.2 guardrail: no invoice with a CRITICAL finding auto-approves.
- **HIGH** — a substantive defect that requires human judgment.
- **MEDIUM** — an anomaly worth surfacing to the Adjudicator.
- **LOW / INFO** — noted; rarely decision-relevant on its own.

Severities below reflect the Phase 4 baseline calibration. Reassess in Phase 7 once the eval harness runs, and record any change as a DECISIONS entry.

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
| `AR-001` | MEDIUM | Line total ≠ `quantity × unit_price` | Both present, difference > $0.01 | A stated line amount that doesn't equal its own price × quantity is either a keying error or a fraud handle. | (none in corpus) |
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
| `PR-002` | LOW | Unit price < reference × (1 − tolerance) | `unit_price.amount_usd < reference × 0.95` | Discount below contract — worth flagging but usually not blocking. | INV-1013 volume-discount lines |
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
| `TM-002` | LOW | Stated terms ≠ contracted terms for the master vendor | Compare `payment_terms` to `vendors.contracted_terms` | Ad-hoc term change worth surfacing to AP. | (none currently in corpus) |
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
