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
- [ ] `src/schema.py` — `LineItem`, `Invoice`, `Finding`, `Decision`, `RunRecord`. Every monetary field carries `amount_native` + `amount_usd` + `currency`
- [ ] Field-level `confidence` and `provenance` on extracted values
- [ ] `corrections[]` on `Invoice` — every silent repair the extractor makes (OCR character substitution, inferred field, normalized identifier) recorded with the original and corrected value, so validators can raise `EX-` findings rather than losing the signal
- [ ] **Read `generate_pdfs.py` before writing any adapter.** Record in `DECISIONS.md`:
  - [ ] whether the OCR corruption (`2O26`, `$3,500.O0`) is injected deliberately, and the exact substitution rule — this determines whether O→0 normalization is narrow (numeric tokens only) or global
  - [ ] whether the txt/pdf duplicate pairs are a deliberate trap or a rendering artifact
  - [ ] any embedded source literals — these become exact ground truth for INV-1011/1012/1013
- [ ] `src/store/seed.py` — `inventory` (with `reference_unit_price`) and `vendors` tables per `CLAUDE.md` §6
- [ ] Item-name canonicalization helper (`Widget A`, `WidgetB`, `WidgetA (rush order)` → `WidgetA`)
- [ ] `make seed` rebuilds the reference DB from scratch

**Exit:** `sqlite3 reference.db` shows both tables populated; generator findings logged.

## Phase 2 — LLM layer (before any LLM call is made)
- [ ] `provider.py` — Grok via OpenAI-compatible SDK; Claude path defined but unconfigured
- [ ] `cassette.py` — record to `data/cassettes/`, replay offline
- [ ] **The cassette key hashes the prompt text**, so editing a prompt forces a cache miss and a live call. Without this you will debug changes that never took effect.
- [ ] Retry, timeout, token/latency/cost capture on every call

**Exit:** a recorded call replays with zero network access. Every subsequent phase records as it goes.

## Phase 3 — Format adapters
- [ ] `json_adapter` — handles missing `amount` fields (INV-1005) and the 8-line-item case (INV-1013)
- [ ] `csv_adapter` — **both** schemas: vertical key–value with repeated keys (INV-1006), and row-per-item with trailing summary rows (INV-1007, INV-1015)
- [ ] `xml_adapter` — including the EUR path (INV-1014)
- [ ] `text_adapter` — LLM extraction, driven by `prompts/extractor.md`
- [ ] `pdf_adapter` — pdfplumber → text → LLM extraction
- [ ] Triage router: extension → adapter, with LLM fallback on parse failure or low field coverage
- [ ] Schema repair loop — feed Pydantic validation errors back to the model, max 2 attempts
- [ ] Test per adapter against its known trap

**Exit:** all 20 files produce a canonical `Invoice`. Extraction correctness not yet measured.

## Phase 4 — Validators
Each is pure: `(Invoice, reference) -> list[Finding]`.

- [ ] `arithmetic.py` — line totals, subtotal, tax, grand total, negative quantities, negative totals, non-line-item charges (INV-1010 shipping)
- [ ] `inventory.py` — **aggregate by canonical item within invoice**, then check stock; unknown item; zero-stock item
- [ ] `pricing.py` — vs reference ±5%, post-FX; intra-invoice price inconsistency; missing reference
- [ ] `vendor.py` — master lookup, fuzzy match without exact match, domain mismatch, inactive-vendor rename claim, missing name
- [ ] `terms.py` — due date vs `date + terms` (±2 days), terms vs contract, unparseable/past due dates
- [ ] `duplicates.py` — `(vendor, invoice_number)` with content hash; identical-file vs differing-content vs revision-marker
- [ ] `policy.py` — threshold, near-threshold band, FX applied
- [ ] `signals.py` — urgency language, wire-transfer requests, suspicious address
- [ ] Test each validator against its trigger invoice

**Exit:** every invoice in the ground-truth table produces its expected findings. This is the first real checkpoint.

## Phase 5 — Graph and judgment agents
- [ ] `src/graph.py` — `StateGraph` assembled so it reads like the flow diagram
- [ ] `src/tools/` — five read-only lookup tools per `CLAUDE.md` §2.1a, registered as LangGraph tools with JSON schemas
- [ ] `prompts/adjudicator.md` — receives invoice + findings + policy context, may call tools to investigate, returns decision + rationale. Computes nothing.
- [ ] Tool calls captured in the run trace (name, arguments, result, latency)
- [ ] `prompts/critic.md` — argues the opposite side
- [ ] Conditional critic edge: fires only when amount > threshold **or** any finding ≥ HIGH. Max 2 rounds.
- [ ] Hard guardrail in code: CRITICAL finding cannot resolve to `APPROVE`
- [ ] `prompts/scribe.md` — plain-English exception note for the human queue

**Exit:** all 16 invoices produce a decision with rationale.

## Phase 6 — Gate, settlement, audit
- [ ] `interrupt()` human gate with `SqliteSaver` checkpointing; auto-resolves from fixture in demo mode so the reviewer's run never hangs
- [ ] `mock_payment` on approve; logged rejection with reasoning on reject
- [ ] `RunRecord` written for every invoice: nodes fired, model calls, tokens, latency, cost, findings, rationale, critic exchange, final decision
- [ ] `rich` CLI output — stages tick, findings surface, reasoning prints, decision lands

**Exit:** `python main.py --batch` processes the corpus end to end. **Sync with planning chat before Day 2.**

---

# DAY 2 — Make it defensible

## Phase 7 — Taxonomy and tuning
- [ ] `docs/exception-taxonomy.md` — every code, severity, detection method, business rationale
- [ ] Tune severities against the ground-truth table until the decision distribution matches: 5 approve / 7 reject / 5 escalate

## Phase 8 — Observability
- [ ] Structured JSON run logs alongside `rich` output
- [ ] Per-run cost and latency rollup
- [ ] `make report` prints corpus-level stats: straight-through rate, exceptions by category, cost per invoice, mean latency

## Phase 9 — Evaluation
- [ ] `eval/ground_truth.yaml` — every invoice, every field, expected findings, expected decision. Derive INV-1011/1012/1013 from `generate_pdfs.py` source literals, not from reading the rendered PDFs
- [ ] **Integrity rail:** generator source data is eval-only. It must never be reachable from the extraction path, or the accuracy numbers are fiction
- [ ] `eval/run_eval.py` — field-level extraction accuracy, finding precision/recall, decision accuracy
- [ ] `docs/eval-results.md` with real numbers and a named failure analysis
- [ ] Extrapolate against Acme's baseline: 30% error rate, 5-day cycle, $2M/year

**Exit:** you can state "X% field accuracy, Y% decision accuracy, $Z per invoice" and defend each number.

## Phase 10 — Above and beyond
- [ ] 3–4 adversarial invoices of your own, each targeting a gap the provided corpus misses. **Generate them with a fork of their script** (`scripts/generate_test_pdfs.py`, leaving `generate_pdfs.py` untouched) so they carry identical rendering quirks and extraction difficulty
- [ ] Cross-invoice threshold-structuring detection (INV-1008 and INV-1012 both sitting just under $10K)
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
