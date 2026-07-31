# PLAN.md

Build spine. Claude Code updates checkboxes as work completes. This is the "where are we" file — read it at the start of every session, update it at the end.

**Deadline:** 5 days given, 3 days targeted.
**Client expectation:** "a few hours to a day." Everything past a working pipeline is discretionary and must earn its place.

---

## Definition of done

- [ ] `git clone && make demo` runs the full 16-invoice corpus offline with no API key, in under 60 seconds
- [ ] `python main.py --invoice_path=data/invoices/invoice_1012.txt` works exactly as the brief specifies
- [ ] Dashboard renders the queue and a per-invoice detail view
- [ ] `DECISIONS.md` has an entry for every non-obvious fork
- [ ] README opens with a 60-second orientation and closes with what was cut and why
- [ ] Tests pass; no secrets committed

---

## Pre-committed cut list

If Day 3 runs short, these get cut **in this order**, and each cut gets a line in the README's scope section. Deciding this now is the point.

1. FMEA document (`docs/fmea.md`)
2. Cross-invoice threshold-structuring detection
3. Grok-vs-Claude comparison run
4. Dashboard invoice-source pane (keep the queue + decision detail)
5. Adversarial test invoices beyond the provided 16

Never cut: cassette replay, the eval harness, `DECISIONS.md`, the README.

---

# DAY 1 — Working pipeline, end to end

Goal: all 16 invoices produce a decision. Ugly is acceptable. Nothing below is polish.

## Phase 0 — Scaffold
- [x] Repo structure per `CLAUDE.md` §7
- [x] `requirements.txt`, `.env.example`, `.gitignore`, `Makefile` skeleton
- [x] `src/config.py` with all constants from `CLAUDE.md` §4
- [x] GitHub repo created first; work happens inside the clone, not transferred later
- [x] `.gitignore` before anything else: `.env`, `__pycache__/`, `.venv/`, `*.db`, `runs/`
- [x] xAI key in `.env`, exact model ID pinned from the console (`grok-4.5`)
- [x] **Capability probe** (`scripts/probe.py`) — verify all three before building on any of them:
  - [x] basic completion returns text
  - [x] structured output returns valid JSON against a representative Pydantic schema, with no `minLength`/`maxItems`/`pattern` constraints in the sent schema
  - [x] tool calling round-trips: model requests a tool, receives the result, produces a final answer
- [x] `DECISIONS.md` initialized with the two stale-brief entries (SDK snippet, `grok.x.ai` vs `api.x.ai`)
- [x] Initial commit

**Exit:** all three capabilities confirmed on your account. **Grok is the only configured provider — do not develop against Claude at any point.** If a capability misbehaves, restructure around it rather than switching models: constrained structured outputs fall back to plain JSON mode plus the Pydantic repair loop, which is already in the plan.

## Phase 0b — Extraction baseline (~20 min, before any prompt is written)

Run a throwaway extraction against the four hardest documents, three times each, using a minimal prompt. Log raw outputs to `scripts/probe_output/` (not cassettes — the recording layer doesn't exist yet). The goal is to learn how much work `prompts/extractor.md` is going to be.

| Document | Failure mode under test |
|---|---|
| `invoice_1012.txt` | **Silent correction** — does `2O26` come back as `2026` and `$3,500.O0` as `$3,500.00` with no indication a repair happened? |
| `invoice_1013.pdf` | **Line-item collapsing** — are the three WidgetA lines merged into one? Log the intermediate pdfplumber text separately so a mangled table is attributable to pdfplumber, not Grok |
| `invoice_1003.txt` | **Date hallucination** — does `Due Date: yesterday` become `null`, or does the model invent a plausible date? |
| `invoice_1002.txt` | **Normalization drift** — does `Inv #: 1002` return as `1002` or `INV-1002`? The dedupe key depends on consistency |

- [x] Each document run 3× — flag any run-to-run variance in quantities or totals; if present, drop temperature before proceeding
- [x] Record all five behaviors in `DECISIONS.md`
- [x] Decide and record: does the extractor need a `corrections[]` field so silent repairs surface as `EX-` findings? (Default: yes)

**Exit:** you know whether `prompts/extractor.md` is a two-hour problem or a half-day one, and Phase 3 is scoped accordingly. This baseline is also README material — benchmarking the model on the hardest inputs before writing the extractor is a decision worth showing.

## Phase 1 — Schema, reference data, and corpus forensics
- [x] `src/schema.py` — `LineItem`, `Invoice`, `Finding`, `Decision`, `RunRecord`. Every monetary field carries `amount_native` + `amount_usd` + `currency`
- [x] Field-level `confidence` and `provenance` on extracted values
- [x] `corrections[]` on `Invoice` — every silent repair the extractor makes (OCR character substitution, inferred field, normalized identifier) recorded with the original and corrected value, so validators can raise `EX-` findings rather than losing the signal
- [x] **Read `generate_pdfs.py` before writing any adapter.** Record in `DECISIONS.md`:
  - [x] whether the OCR corruption (`2O26`, `$3,500.O0`) is injected deliberately, and the exact substitution rule — this determines whether O→0 normalization is narrow (numeric tokens only) or global
  - [x] whether the txt/pdf duplicate pairs are a deliberate trap or a rendering artifact
  - [x] any embedded source literals — these become exact ground truth for INV-1011/1012/1013
- [x] `src/store/seed.py` — `inventory` (with `reference_unit_price`) and `vendors` tables per `CLAUDE.md` §6
- [x] Item-name canonicalization helper (`Widget A`, `WidgetB`, `WidgetA (rush order)` → `WidgetA`)
- [x] `make seed` rebuilds the reference DB from scratch

**Exit:** `sqlite3 reference.db` shows both tables populated; generator findings logged.

## Phase 2 — LLM layer (before any LLM call is made)
- [x] `provider.py` — Grok via OpenAI-compatible SDK; Claude path defined but unconfigured
- [x] `cassette.py` — record to `data/cassettes/`, replay offline
- [x] **The cassette key hashes the prompt text**, so editing a prompt forces a cache miss and a live call. Without this you will debug changes that never took effect.
- [x] Retry, timeout, token/latency/cost capture on every call

**Exit:** a recorded call replays with zero network access. Every subsequent phase records as it goes.

## Phase 3 — Format adapters
- [x] `json_adapter` — handles missing `amount` fields (INV-1005) and the 8-line-item case (INV-1013)
- [x] `csv_adapter` — **both** schemas: vertical key–value with repeated keys (INV-1006), and row-per-item with trailing summary rows (INV-1007, INV-1015)
- [x] `xml_adapter` — including the EUR path (INV-1014)
- [x] `text_adapter` — LLM extraction, driven by `prompts/extractor.md`
- [x] `pdf_adapter` — pdfplumber → text → LLM extraction
- [x] Triage router: extension → adapter, with LLM fallback on parse failure or low field coverage
- [x] Schema repair loop — feed Pydantic validation errors back to the model, max 2 attempts
- [x] Test per adapter against its known trap

**Exit:** all 20 files produce a canonical `Invoice`. Extraction correctness not yet measured.

## Phase 4 — Validators
Each is pure: `(Invoice, reference) -> list[Finding]`.

- [x] `arithmetic.py` — line totals, subtotal, tax, grand total, negative quantities, negative totals, non-line-item charges (INV-1010 shipping)
- [x] `inventory.py` — **aggregate by canonical item within invoice**, then check stock; unknown item; zero-stock item
- [x] `pricing.py` — vs reference ±5%, post-FX; intra-invoice price inconsistency; missing reference
- [x] `vendor.py` — master lookup, fuzzy match without exact match, domain mismatch, inactive-vendor rename claim, missing name
- [x] `terms.py` — due date vs `date + terms` (±2 days), terms vs contract, unparseable/past due dates
- [x] `duplicates.py` — `(vendor, invoice_number)` with content hash; identical-file vs differing-content vs revision-marker
- [x] `policy.py` — threshold, near-threshold band, FX applied
- [x] `signals.py` — urgency language, wire-transfer requests, suspicious address
- [x] Test each validator against its trigger invoice

**Exit:** every invoice in the ground-truth table produces its expected findings. This is the first real checkpoint.

## Phase 5 — Graph and judgment agents
- [x] `src/graph.py` — `StateGraph` assembled so it reads like the flow diagram (Phase 5a)
- [x] `src/tools/` — five read-only lookup tools per `CLAUDE.md` §2.1a, registered as LangGraph tools with JSON schemas (Phase 5b)
- [x] `prompts/adjudicator.md` — receives invoice + findings + policy context, may call tools to investigate, returns decision + rationale. Computes nothing. (Phase 5c)
- [x] Tool calls captured in the run trace (name, arguments, result, latency) (Phase 5c)
- [x] `prompts/critic.md` — argues the opposite side (Phase 5c)
- [x] Conditional critic edge: fires only when amount > threshold **or** any finding ≥ HIGH. Max 2 rounds. (Phase 5a)
- [x] Hard guardrail in code: CRITICAL finding cannot resolve to `APPROVE` (Phase 4 predicate wired via `policy_gate` in 5a, enforced in adjudicate in 5c)
- [x] `prompts/scribe.md` — plain-English exception note for the human queue (Phase 5c)

**Exit:** all 16 invoices produce a decision with rationale.

## Phase 6 — Gate, settlement, audit
- [x] `interrupt()` human gate with `SqliteSaver` checkpointing; auto-resolves from fixture in demo mode so the reviewer's run never hangs
- [x] `mock_payment` on approve; logged rejection with reasoning on reject
- [x] Queryable audit persistence (`src/store/audit.py`): runs / findings / model_calls / tool_calls / settlements as related tables, indexed
- [x] Settlement idempotency on `(invoice_number, vendor_name)` via `prior_paid_settlement()`
- [x] `get_prior_invoice` reads real audit data; distinguishes empty-store from no-prior-of-this-number via `store_populated` (fixes the INV-1004 REJECT-on-empty-store bug)
- [x] `rich` CLI output — stages tick, findings surface, reasoning prints, decision lands
- [x] Cold-clone `make demo`: `LLM_MODE=replay HUMAN_GATE_MODE=demo`, no `.env` required, deterministic on repeat runs (checkpoint DB cleared at demo start)
- [x] Cassette portability: `_invoice_summary` emits `source_file` as basename so recordings are machine-independent

**Exit:** `python main.py --batch` processes the corpus end to end. **Sync with planning chat before Day 2.**
**Exit met:** batch produces 4 APPROVE / 10 ESCALATE / 2 REJECT at $1.12406 (live) / <2s (cold replay). Two consecutive `make demo` runs produce byte-identical output.

---

# DAY 2 — Make it defensible

## Phase 7 — Taxonomy and tuning
- [x] `docs/exception-taxonomy.md` — every code, severity, detection method, business rationale, observed-corpus behavior, distribution reconciliation
- [x] Codes not exercised by corpus flagged honestly (AR-001, AR-003, PR-002)
- [x] AR-004 signed-delta distinction documented with both corpus cases (INV-1013 overcharge, INV-1007 undercharge)
- [~] Severity retuning **deferred** — severity strings are part of the LLM request fingerprint, so any change invalidates cassettes. Recorded proposals in DECISIONS 2026-07-31 Phase 7 rather than applying. Zero live spend this session by design.
- [x] Distribution reconciliation: 4/10/2 at Adjudicator boundary → 5 PAID / 4 REJECTED / 7 HOLD after demo human gate. Every divergence from original expectation defended per-invoice.

## Phase 8 — Observability
- [x] Structured JSON run logs alongside `rich` output — `runs/batch-YYYYMMDDTHHMMSS.jsonl`, manifest line + one line per invoice (outcome, findings, nodes fired, per-model-call token breakdown, tool calls with latency, cost, wall clock, terminal_status)
- [x] Per-run cost and latency rollup — already surfaced in the CLI batch summary and JSONL records; per-invoice-per-node query verified against the Phase 6 audit schema (no schema change needed)
- [x] `make report` prints corpus-level stats: outcome distribution, straight-through rate, queue depth, exceptions by category, cost by node type, token breakdown, per-invoice cost (pulled forward from Phase 8 into Phase 7 since audit schema was ready)
- [x] Manifest header at run start: model, mode, cassette count, config constants (threshold / tolerances / FX with as-of / caps / human-gate mode), git SHA + dirty flag
- [x] Failure-path observability locked in with tests: cache-miss includes key + fix hint; circuit-breaker names cap value + prompt_name; no-decision path lands FAILED terminal_status with reason, persists to audit store when invoice present

## Phase 9 — Evaluation
- [x] `eval/ground_truth.yaml` — 16 invoices with expected extraction fields, must-fire / may-fire finding codes, expected-outcome sets (single- and multi-valued per the Phase 7 reconciliation)
- [x] **Integrity rail:** generator source data is eval-only — `test_src_never_imports_eval` still passes after Phase 9
- [x] `eval/run_eval.py` — field-level extraction accuracy, must-fire finding coverage, decision agreement; JSON summary under `runs/eval-<ts>.json`
- [x] `make eval` regression gate — nonzero exit if any must-fire miss or any single-valued outcome divergence
- [x] `docs/eval-results.md` — 99.2% extraction / 100% must-fire / 100% decision agreement; per-format breakdown; every miss named with one-line analysis; business extrapolation with assumptions stated
- [x] Extrapolate against Acme's baseline: 30% error rate, 5-day cycle, $2M/year — done in eval-results.md, framed against the two loss drivers (cycle time, missed-defect cost) rather than a fantasy dollar number

**Exit:** you can state "X% field accuracy, Y% decision accuracy, $Z per invoice" and defend each number.

## Phase 10 — Above and beyond
- [x] 4 adversarial invoices in `data/adversarial/`, each targeting a gap the provided corpus misses (AR-001, AR-003, PR-002, PO-002-pair). Formats limited to txt/csv/json — no PDF-generator fork (stayed cut per Phase 10 scope decision; the proven adapters cover the target codes without introducing new rendering variance).
- [~] Cross-invoice threshold-structuring detection — **not implemented.** The adversarial pair ADV-2001/ADV-2004 (same vendor, 3 days apart, each sub-threshold, $19,425 jointly) fires PO-002 on each invoice individually but no aggregator recognizes the pair. Documented in eval-results as a known gap; a cross-invoice detector is Phase 11+ work.
- [ ] *Optional, first to cut:* run the finished eval suite against Claude for comparison. State the bias plainly — prompts were developed against Grok and Claude is being measured on Grok-tuned prompts

**Exit:** **Sync with planning chat before Day 3.**

---

# DAY 3 — Presentation

## Phase 11 — Dashboard
- [ ] FastAPI + Jinja + Tailwind CDN + Alpine.js, styled per `CLAUDE.md` §3b. No build step.
- [ ] Adjudicator tool calls rendered in the trace view — what it asked, what came back
- [ ] Queue view: all invoices, status chips, straight-through rate, exceptions by category, cost per invoice
- [ ] Detail view: source document left, extracted fields with confidence right, findings below, adjudicator rationale, critic challenge, final decision
- [ ] Human review queue with the Scribe's note and approve/reject/hold actions
- [ ] INV-1004 vs INV-1004-R1 rendered as a side-by-side diff — this is the demo centerpiece

## Phase 12 — README
Assembled from `DECISIONS.md`. Drafted in the planning chat, not by Claude Code.
- [ ] 60-second orientation: what this is, how to run it
- [ ] Current-state map and the exception-routing reframe
- [ ] Architecture diagram + the deterministic/LLM boundary stated explicitly
- [ ] Exception taxonomy summary
- [ ] Measured results and business impact
- [ ] Assumptions made explicit (stock as point-in-time, derived reference prices, seeded vendor master, static FX)
- [ ] What was cut and why
- [ ] `docs/how-this-was-built.md` — the agent-directed workflow, the `CLAUDE.md` / `PLAN.md` / `DECISIONS.md` triad, and where the agent was overridden
- [ ] `docs/fmea.md` if time permits

## Phase 13 — Ship
- [ ] Cold-clone test on a clean checkout with no `.env`: `make demo` must work
- [ ] Full test suite green
- [ ] Secrets scan
- [ ] Commit history reviewed — incremental, real messages, no squash
- [ ] Push, verify the public URL loads for a logged-out visitor, send the link

---

## Session log

Claude Code appends one line per session: date, phases touched, anything that surprised you.

2026-07-29 — Phase 0 + 0b complete. Scaffold, config, probe, extraction baseline. Surprises: `grok-4.5` doesn't follow the dated-identifier convention; INV-1003 "yesterday" returned as literal string rather than null (no hallucination); INV-1012 silent correction confirmed — `corrections[]` field needed in schema.

2026-07-29 — Phase 1 complete. Schema, canonicalization, seed. Surprises: INV-1013 carries a deliberate +$50 grand-total error in BOTH the JSON and the PDF that CLAUDE.md §6 doesn't call out — this is an additional AR- finding on top of the aggregate stock overrun. INV-1011 PDF really is less complete than the txt source (no subtotal/tax line generated). 30 tests pass.

2026-07-29 — Phase 2 complete. Provider (retry + cost/latency capture) + cassette (key hashes prompt text; auto/live/replay modes; credential redaction). Live smoke round-trip works end-to-end (`pong` cassette recorded). Surprises: `grok-4.5` resolves to `grok-4.5` (not a dated build) on this account — proven by the ModelCall capture; a trivial single-word reply cost 214 input tokens (system-prompt overhead is not zero). 58 pass, 2 skip.

2026-07-29 — Phase 3 complete. All 20 corpus files produce Invoices. Deterministic adapters (json/csv/xml) + LLM adapters (text/pdf) + triage router + repair loop. prompts/extractor.md now instructs declared repairs — INV-1012 produces 2 explicit corrections for the OCR substitutions (previously silent). No LLM fallbacks fired on this corpus (all deterministic parsers cleared MIN_FIELD_COVERAGE=0.75). Both previously-skipped hash tests pass end-to-end. Surprise: `.env` had LLM_MODE=live from Phase 2 dev, making Phase 3 test runs 150+ seconds per invocation until switched to auto. 110 pass, 0 skip.

2026-07-29 — Phase 4 complete. All 8 validators + registry + guardrail predicate. Findings match CLAUDE.md §6 with one discovery: INV-1007 has an undocumented $110 grand-total arithmetic error (14750 subtotal + 885 tax = 15635, stated total 15525). Every invoice produces expected findings; INV-1006 remains clean (fuzzy VN-002 does not false-positive on Acme Industrial); INV-1010's $150 shipping correctly does NOT trip AR-004. 145 pass.

2026-07-30 — Phase 5a complete. Graph skeleton (StateGraph with SqliteSaver checkpointer), deterministic nodes (triage, validate, policy_gate, route_outcome placeholder), stubs (adjudicate, critique) with conditional edge on CRITIC_TRIGGER, batch pre-pass for duplicates. Zero API calls this phase. Collapsed triage+extract into a single triage node. 157 pass.

2026-07-30 — Phase 5b complete. Five read-only tools registered in a single TOOLS list: get_vendor_record, get_vendor_invoice_history, get_item_reference, get_prior_invoice, get_policy. Extracted `score_candidates()` helper so validator (strict threshold 0.70) and tool (investigative threshold 0.30) share one SequenceMatcher implementation. Minimal SQLite AuditStore that Phase 6 will fill with RunRecord data. Zero API calls. 177 pass.

2026-07-30 — Phase 5c complete. Adjudicator + Critic + Scribe wired with tool calling; §2.2 hard guardrail enforced in adjudicate code after the model returns. Full corpus adjudicated: 4 APPROVE / 10 ESCALATE / 2 REJECT vs the 5/7/5 target — the divergence is toward ESCALATE, consistent with the "escalate liberally" prompt instruction. Cost $0.55 total, ~$0.035/invoice. Total tests passing across the whole suite.
