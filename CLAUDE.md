# CLAUDE.md

Standing context for this repository. Read this file at the start of every session before doing anything else.

---

## 1. What this is

An agentic accounts-payable system built for Acme Corp, a PE-backed manufacturer losing ~$2M/year to manual invoice processing (30% error rate, 5-day cycle time).

**The organizing idea: this is an exception-routing system, not an invoice-processing system.**

The clerk's problem is not keystrokes. It is that every invoice requires a judgment call made without scaffolding. The win is separating the invoices that need no human from the ones that do, and handing humans the second group with the evidence pre-assembled and the reasoning already written.

Everything in this codebase should serve that framing.

---

## 2. Non-negotiable architecture rules

### 2.1 The deterministic / LLM boundary

This is the most important rule in the repository. Violating it is the difference between a shipped system and a demo.

**The LLM may request facts through typed, read-only tools. It may not compute, and it may not apply policy thresholds.** Tools return data, never decisions. Specifically:

| Task | Owner |
|---|---|
| Extracting fields from unstructured text or PDF | LLM |
| Judging whether a set of findings is disqualifying | LLM |
| Writing the human-readable exception note | LLM |
| Parsing JSON / CSV / XML | Deterministic |
| All arithmetic (line totals, subtotals, tax, grand total) | Deterministic |
| Inventory lookups and quantity aggregation | Deterministic |
| Price comparison against reference prices | Deterministic |
| Vendor master lookup and fuzzy matching | Deterministic |
| Currency conversion | Deterministic |
| Date arithmetic and payment-terms consistency | Deterministic |
| Duplicate detection | Deterministic |
| The $10,000 approval threshold | Deterministic |

If you find yourself writing a prompt that asks the model to add numbers, compare a quantity to stock, or apply a dollar threshold — stop. That belongs in `src/validators/`.

### 2.1a Adjudicator tools

The Adjudicator receives the invoice and its findings, and may call these read-only tools to investigate further. Each returns deterministic data from the reference or audit store. None returns a judgment.

| Tool | Returns |
|---|---|
| `get_vendor_record(name)` | Master record or null, plus fuzzy candidates with match scores |
| `get_vendor_invoice_history(name)` | Prior invoice count, totals, first/last seen |
| `get_item_reference(item)` | Stock, reference unit price, category, active flag |
| `get_prior_invoice(invoice_number)` | Prior submission under the same number, if any |
| `get_policy(finding_code)` | Policy text and severity rationale for a finding code |

This satisfies the brief's function-calling requirement and materially improves reasoning quality: on INV-1012 the Adjudicator can discover that QuickShip is absent from the master, that FastShip Ltd. is a fuzzy candidate, and that FastShip's relationship went dormant — an investigative chain that a pre-computed findings list cannot produce. Every tool call is captured in the run trace and surfaced in the dashboard.

### 2.2 Hard guardrail on auto-approval

The Adjudicator may downgrade or escalate, but **it can never auto-approve an invoice carrying a CRITICAL deterministic finding.** That rail is enforced in code after the LLM returns, not in the prompt. An LLM that can be talked into approving INV-1003 is not a system anyone will deploy.

### 2.3 Three outcomes, not two

`APPROVE` / `REJECT` / `ESCALATE`. The case brief only asks for two. Escalation is a deliberate extension: wrongly rejecting a legitimate invoice costs a vendor relationship, wrongly approving a fraudulent one costs money, escalating costs four minutes of a clerk's time. The system should escalate liberally and auto-decide only when confident.

### 2.4 Cheap work first

Deterministic parse before LLM extraction. LLM extraction only for `.txt` and `.pdf`, or as a **repair fallback** when a structured parse fails, returns low field coverage, or produces a schema-invalid record. Do not route JSON/CSV/XML through the model by default.

### 2.5 The reviewer will run this with no API key

`make demo` must execute the full corpus offline, deterministically, in seconds, using committed cassettes. `--live` hits the real API. If the cold-clone path breaks, nothing else in this repo matters.

---

## 3. Stack (pinned — do not substitute)

- **Python 3.11+**
- **LangGraph** — `StateGraph`, `SqliteSaver` checkpointer, `interrupt()` for the human gate
- **Pydantic v2** — canonical schema and validation
- **xAI Grok** via the **OpenAI-compatible SDK** (`openai` package, `base_url="https://api.x.ai/v1"`). Model ID comes from `GROK_MODEL` in env — pin an exact dated identifier from the console, never an alias.
- **Provider abstraction** in `src/llm/provider.py` — thin interface so Grok and Claude are swappable. Approximately 30 lines. Do not build an abstraction framework. **Grok is the only configured provider. Never develop, test, or tune a prompt against Claude** — prompts tuned on one model and run on another fail silently, returning valid JSON with subtly wrong values. The Claude path exists as documentation of portability, not as a development target.
- **pdfplumber** for PDF text extraction
- **SQLite** for inventory, vendors, and the audit store
- **rich** for CLI output
- **FastAPI + Jinja2 + Tailwind CDN + Alpine.js** for the dashboard. No build step, no npm, no Next.js. See §3a.
- **pytest**

### 3a. xAI API constraints (verify in the Phase 0 probe)

Two details in the case brief are stale — log both in `DECISIONS.md`:
- `from xai import Grok` is not the current SDK. Use the `openai` package against `api.x.ai/v1`.
- `https://grok.x.ai` is the consumer chat product, not the API endpoint.

Known platform constraints:
- **Structured outputs do not support** `minLength` / `maxLength` on strings, `minItems` / `maxItems` on arrays, or `pattern` constraints. Keep these out of any Pydantic model whose schema is sent to the API; enforce them in a post-validation pass instead.
- Use `max_completion_tokens`, not `max_tokens`. `stop`, `presence_penalty`, and `frequency_penalty` are unsupported.
- Streaming cannot be combined with tool calling or `response_format`. We do not stream — do not add it.

### 3b. Dashboard standards

No build step is a reliability decision, not a quality ceiling. Design quality comes from these:

- Tailwind CDN plus a custom CSS variable layer: `#0f172a` slate-900 primary, `#1d4ed8` blue-700 accent, white cards, amber warnings
- Playfair Display for hero numerics and section titles; Georgia for prose; system sans for UI chrome
- Alpine.js for filtering, the detail drawer, and the diff view — roughly 15KB, no build
- Hand-rolled inline SVG for metric bars; no charting library
- Server-rendered Jinja reading the audit store; nothing to hydrate

---

## 4. Domain constants

Live in `src/config.py`, never inline.

```
APPROVAL_THRESHOLD_USD   = 10_000
NEAR_THRESHOLD_BAND      = 0.05      # flag invoices within 5% below the threshold
PRICE_TOLERANCE          = 0.05      # ±5% against reference unit price
TERMS_TOLERANCE_DAYS     = 2
FX_RATES                 = {"EUR": 1.14}   # as of 2026-07-28, static by design
CRITIC_TRIGGER           = amount > threshold OR any finding severity >= HIGH
MAX_CRITIC_ROUNDS        = 2
MAX_REPAIR_ATTEMPTS      = 2
```

Currency: store both `amount_native` and `amount_usd` on every monetary field so the audit trail shows the conversion rather than hiding it. All thresholds and comparisons operate in USD.

---

## 5. Findings taxonomy

Every deterministic check emits zero or more `Finding` objects: `{code, severity, message, evidence, field_path}`. Severities: `INFO / LOW / MEDIUM / HIGH / CRITICAL`.

| Prefix | Domain |
|---|---|
| `EX-` | Extraction (low confidence, missing field, repair invoked) |
| `AR-` | Arithmetic (line/subtotal/tax/total mismatch, negative qty, negative total) |
| `IN-` | Inventory (unknown item, zero stock, aggregate quantity exceeds stock) |
| `PR-` | Pricing (above/below contract, intra-invoice inconsistency, no reference) |
| `VN-` | Vendor (not in master, fuzzy match without exact, domain mismatch, inactive vendor, missing name) |
| `TM-` | Terms (due date inconsistent with terms, terms differ from contract, unparseable date) |
| `DP-` | Duplicates (identical file, same number differing content, revision marker) |
| `PO-` | Policy (exceeds threshold, near threshold, FX applied) |
| `FR-` | Fraud signals (urgency language, non-standard payment channel, suspicious address) |

The full table with severity assignments lives in `docs/exception-taxonomy.md`. Codes are stable identifiers — never renumber.

---

## 6. Corpus facts (do not rediscover these badly)

Sixteen invoices, twenty files. Detailed ground truth in `eval/ground_truth.yaml`.

- **Three invoices exist as duplicate file pairs** — INV-1011 (txt+pdf), INV-1012 (txt+pdf), INV-1013 (json+pdf). Naive batch processing double-pays $35,537. The INV-1011 PDF is *less complete* than its txt source (no subtotal/tax lines), so dedupe must reconcile differing extractions, not just match hashes. Prefer the more complete record; log the discrepancy.
- **INV-1004 appears twice with different totals** — `invoice_1004.json` ($1,890) and `invoice_1004_revised.json` ($5,940, `revision: R1`). Both internally clean. Cannot be resolved deterministically. This is the flagship escalation case.
- **INV-1013 passes every per-line stock check and fails catastrophically in aggregate** — WidgetA 22/15, WidgetB 18/10, GadgetX 9/5 across eight line items. Quantities must be aggregated by canonical item name *within* an invoice before the stock check. **It also carries a deliberate +$50 grand-total error** injected by the generator (`grand_total + 50`): line items sum to 21,040, tax at 7% is 1,472.80, so the total should be 22,512.80 — the document states 22,562.80. The same offset is baked into `invoice_1013.json`. This invoice trips both `IN-` and `AR-` findings on independent axes.
- **The txt/pdf duplicate pairs are deliberate, not artifact.** The generator runs exactly three functions for INV-1011, INV-1012, and INV-1013. No other invoice has a PDF companion. This is a hand-picked duplicate-detection trap set and should be described as such in the README.
- **INV-1012 is arithmetically perfect and behaviorally suspicious** — OCR letter-O-for-zero (`2O26`, `$3,500.O0`), item spacing variants (`Widget A` / `WidgetB` / `Gadget X`), a "formerly FastShip Ltd." rename claim, and a $9,975 total sitting $25 under the threshold. FastShip Ltd. is seeded as an *inactive* real vendor, which makes this genuinely ambiguous rather than obviously fake. **The corruption is confirmed deliberate** — the generator injects capital O for digit 0 in exactly two tokens. Normalization must be **narrow** (inside otherwise-numeric or currency tokens only); the same document contains legitimate capital Os in `INVOICE`, `NOTES`, `TOTAL`, and `Payble`, so global substitution would corrupt real text.
- **INV-1008 sits $100 under the threshold.** Two invoices immediately below $10,000 is a threshold-structuring pattern worth detecting.
- **CSVs use two incompatible schemas** — INV-1006 is vertical key–value with repeated `item` keys (naive `DictReader` silently drops a line item); INV-1007 and INV-1015 are row-per-item with trailing summary rows.
- **INV-1014 is denominated in EUR.** WidgetB at €475 converts to $541.50, which is 8.3% *over* contract — comparing native to reference would read it as a discount. Currency handling flips the sign of the finding.
- **INV-1010 has a shipping charge outside the line items** and a rush-order line at $300 vs $250 reference.
- **INV-1005 lists the White House as the vendor address.**

### Documented assumptions

1. **Stock is a point-in-time availability check, not a depleting pool.** Aggregate corpus demand for WidgetA is ~90 units against 15 in stock. Decrementing inventory would make everything after the first two invoices fail. Check against standing stock; do not deplete.
2. **Reference unit prices ($250 / $500 / $750) were derived from the modal price across the corpus,** not supplied by the client. Say so in the README.
3. **The vendor master is seeded to reflect a plausible established-manufacturer relationship set.** In production this is the ERP vendor master. Four corpus vendors are deliberately absent so the unknown-vendor check has teeth.

---

## 7. Repository conventions

```
main.py                 CLI entry — honors --invoice_path, adds --batch, --live, --replay
src/config.py           All constants
src/schema.py           Pydantic canonical models (Invoice, LineItem, Finding, Decision, RunRecord)
src/graph.py            LangGraph assembly — this file should read like the flow diagram
src/nodes/              One file per graph node
src/adapters/           One file per format
src/validators/         One file per check domain, each returns list[Finding]
src/llm/provider.py     Provider abstraction
src/llm/cassette.py     Record/replay
src/tools/              Read-only lookup tools exposed to the Adjudicator (§2.1a)
src/store/              DB, seed, audit
src/ui/                 FastAPI dashboard
prompts/*.md            Every prompt in its own file — never an inline string
data/invoices/          Corpus
data/cassettes/         Committed LLM recordings
docs/                   Exception taxonomy, FMEA, eval results
eval/                   Ground truth + harness
tests/
```

**Prompts live in `prompts/` as markdown files, loaded at runtime.** They are design artifacts and a reviewer will open them first. No f-string prompts buried in node code.

---

## 8. Documentation discipline

After completing any task:

1. **Append to `DECISIONS.md`** if you made a non-obvious choice, rejected an alternative, or hit a failure that changed the approach. Format is exactly three lines — **Decision / Alternatives considered / Why** — plus a date. Do not editorialize. Do not write marketing copy.
2. **Update the relevant checkboxes in `PLAN.md`.**

The failures are the most valuable entries. If something was tried, broke, and was replaced, that gets written down at the moment it happens. It cannot be reconstructed from finished code later.

`DECISIONS.md` is append-only. Never rewrite or tidy prior entries.

---

## 9. Quality bar

- Type hints throughout. Pydantic models for every structured boundary.
- Every validator is pure: takes a canonical `Invoice` plus reference data, returns `list[Finding]`. No side effects, trivially testable.
- Every LLM call is wrapped with retry, timeout, cassette recording, and token/latency/cost capture.
- Every run writes a complete audit record: nodes fired, model calls, findings, rationale, critic challenge, final decision.
- Tests for each adapter against its known trap, each validator against its known trigger invoice.
- Errors are handled and logged, never swallowed. A failed invoice produces a `FAILED` record with a reason, not a stack trace and an exit.

---

## 10. Do not

- Do not add frameworks, abstraction layers, or configuration systems not listed here.
- Do not build a purchase-orders table or three-way matching. Out of scope by decision; note it as understood-but-cut in the README.
- Do not let the dashboard grow past what `docs/` and the audit table can feed it.
- Do not write the README during the build. It is assembled on Day 3 from `DECISIONS.md`.
- Do not commit `.env`, API keys, or `.db` files other than the seeded reference database.
- Do not squash the commit history. Incremental commits with real messages are part of the deliverable.
