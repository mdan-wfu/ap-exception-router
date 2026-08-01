# AP Exception Router

An agentic accounts-payable system that reads invoices in five formats, validates them against inventory, pricing, vendor, and policy rules, and routes each one to automatic payment, rejection, or human review — with the reasoning attached.

The dashboard turns that pipeline into something an AP clerk can actually use: fewer manual errors, shorter processing cycles, and a review queue where the evidence is already assembled.

Built for the Galatiq case study. Grok 4.5 + LangGraph + Python.

---

## Quickstart

**Prerequisites:** Python 3.11+ (`python3 --version`) and `make`. No API key required for the demo path — cassettes replay the corpus offline.

```bash
git clone https://github.com/mdan-wfu/ap-exception-router.git
cd ap-exception-router
make install
make demo
```

**No API key required.** The 16-invoice corpus runs offline in about a second from committed cassettes — real Grok responses, recorded and replayed exactly. Run `make demo-digest-check` to verify: decisions, findings, and costs are locked against a committed baseline.

Then:

```bash
make dashboard    # http://127.0.0.1:8000 — the AP manager's view
make eval         # the scoring harness
make report       # corpus stats: cost, straight-through rate, exception mix
```

To run an invoice the system has never seen, see [Testing with your own invoices](#testing-with-your-own-invoices).

---

## The reframe

Acme's problem is described as invoice processing. It isn't.

The pain points reflect that the process isn't just slow (5-day delays), it's incredibly unreliable (30% error rate). Both issues arise from clerks making unsupported judgment calls on every document, without scaffolding or consistent structure.

To improve the process, this system aims to separate the invoices that need a human from the ones that don't — letting clerks concentrate their effort where it matters — and to assemble the evidence before handing over the ones that do.

As such, this system's job is **exception routing**, not invoice processing:

- Process the clean ones end to end, no human involvement
- Route the rest to a person with the investigation already done and the question already framed
- Never make a call that belongs to a human

That reframe produces one design decision the brief didn't ask for: **three outcomes, not two.**

The spec says approve or reject. Real AP has a third state. Wrongly rejecting a legitimate invoice costs a vendor relationship. Wrongly approving a fraudulent one costs money. Escalating costs a clerk four minutes. The system escalates liberally and auto-decides only when confident.

---

## Architecture

```
triage → validate → policy_gate → adjudicate ⇄ critic → human gate → settle
   │          │           │            │          │           │         │
 5 format  9 validator  CRITICAL   Grok +      opposing   interrupt() mock_payment
 adapters  families     guardrail  5 tools     argument   + resume   or logged
                                                                     rejection
```

### The boundary that matters

**The LLM never computes, and never applies policy.** It extracts from unstructured text, judges whether findings are disqualifying, and writes the human-facing note. Everything else is deterministic Python:

| The model does | Code does |
|---|---|
| Extract fields from txt/PDF | Parse JSON/CSV/XML |
| Judge whether findings are disqualifying | All arithmetic |
| Request facts via read-only tools | Inventory lookups and aggregation |
| Write the exception note | Price comparison, FX conversion |
| | Vendor matching, date logic, duplicate detection |
| | The $10,000 threshold |

Findings arrive at the Adjudicator as settled fact. Its prompt forbids recomputing them. This is intentional — an LLM asked to add a column of figures will do so plausibly, and be wrong at a rate nobody can measure.

### Tools

The Adjudicator has five read-only lookup tools (`get_vendor_record`, `get_vendor_invoice_history`, `get_item_reference`, `get_prior_invoice`, `get_policy`) and decides for itself what it needs to know. Tools return data — never a judgment, never a recommendation. A test scans tool outputs for judgment words and fails if any appear.

The point isn't satisfying the brief's function-calling requirement. It's that investigation beats a static findings list. On INV-1012 the Adjudicator discovers QuickShip is absent from the master, finds FastShip Ltd. named as a claimed predecessor, learns FastShip is inactive with zero payment history, and escalates on that chain — reasoning a pre-computed list can't produce, and that a human can audit afterward.

### The hard guardrail

An invoice carrying any `CRITICAL` finding can never resolve to `APPROVE`. Enforced in code after the model returns, not in the prompt. If the Adjudicator approves past a CRITICAL, the outcome is overridden to `ESCALATE`, the override is recorded, and the model's original rationale is preserved beside it.

---

## What's actually in the corpus

Twenty files, sixteen invoices. Reading the files properly before writing any code turned out to be most of the work, and helped inform several of the build decisions.

**Three invoices exist as duplicate file pairs.** INV-1011 (txt+pdf), INV-1012 (txt+pdf), INV-1013 (json+pdf). Reading `generate_pdfs.py` confirmed these are deliberate — the generator renders exactly three invoices, hand-picked. Naive batch processing double-pays $35,537.

**INV-1013 passes every per-line stock check and fails catastrophically in aggregate.** Eight line items; WidgetA appears three times summing to 22 against 15 in stock, WidgetB 18/10, GadgetX 9/5. Every individual line is valid. Quantities must be aggregated by canonical item *within* the invoice before checking stock. It also carries a deliberate +$50 grand-total error injected by the generator.

**INV-1007 carries a −$110 grand-total error nobody documented.** Found by the validators, not by reading. Note the direction: INV-1013 overcharges, INV-1007 *under*charges. Both warrant a finding; the business impact differs, so `AR-004` records the signed delta.

**INV-1012 is arithmetically perfect and behaviorally suspicious.** OCR corruption (`2O26`, `$3,500.O0`), item-name spacing variants, a "formerly FastShip Ltd." rename claim, and a $9,975 total sitting $25 under the approval threshold. FastShip is seeded as a real-but-inactive vendor, which makes the claim genuinely ambiguous rather than obviously fake. That's the case escalation exists for.

**INV-1004 appears twice with different totals.** `invoice_1004.json` ($1,890) and `invoice_1004_revised.json` ($5,940, marked `revision: R1`). Both internally clean — perfect arithmetic, in-stock quantities, known vendor. Nothing deterministic separates them. Does R1 supersede, or is this double-billing? The system can't know, so it escalates with both versions diffed side by side.

**INV-1014 is denominated in EUR.** WidgetB at €475 converts to $541.50 — 8.3% *over* the $500 contract price. Compared natively it reads as a 5% discount. Currency handling flips the sign of the finding.

**The CSVs use two incompatible schemas**, and one of them breaks `csv.DictReader` by silently dropping a repeated `item` key.

---

## Results

Scored against ground truth derived from the generator's own source literals, not from the system's output. `make eval` reproduces this from a clean checkout with no API key.

| Layer | Result |
|---|---|
| Extraction accuracy | **131 / 131** field-level checks |
| Must-fire findings | **41 / 41** codes |
| Decision agreement | **16 / 16** outcomes |

100% across all three, and 100% per format (CSV, JSON, PDF, TXT, XML).

**The pipeline decides:** 4 approve, 10 escalate, 2 reject. That's the system's judgment unaided.

**After demo human review resolves the queue:** 5 paid, 4 rejected, 7 still held. That's the number an AP manager cares about — and the 25% straight-through rate is volume that never reaches a clerk at all.

**Operationally:** $0.07 per invoice, $1.12 for the full corpus.

A separately-scored [authored adversarial set](docs/eval-results.md#authored-adversarial-set-phase-10) of four invoices targets the three finding codes the provided corpus never exercises. Kept in its own corpus with its own ground truth, never blended into the numbers above.

Full analysis, named misses, and business extrapolation: [`docs/eval-results.md`](docs/eval-results.md).

---

## Designed for a human who will disagree with it

The dashboard assumes an AP clerk with no technical background, who will sometimes overrule the system, sometimes need more information before deciding, and sometimes get it wrong and need to fix it.

- **Hold is a real state, not a resolution.** Setting an invoice aside because you need more information isn't a decision — held invoices stay actionable and get their own view.
- **Decisions append, never overwrite.** Amending a decision writes a new record with a required reason. The original stays visible. The detail view shows the full chain: what the model decided, what the human decided, and every amendment since.
- **The system admits what it can't undo.** If an approval is amended after settlement already fired, the interface surfaces a payment-reversal flag naming the payment reference. It cannot un-call a payment, and pretending otherwise would be worse than useless.
- **Model and human decisions are stored separately.** Where they disagree, both are visible. That disagreement is the most useful signal the system produces for improving it.
- **Finding codes are explained in place.** `IN-003` means nothing to a clerk, so every code carries its plain-English meaning, with a full reference at `/codes`.

---

## What went wrong

One story, because it's the one that took the longest to see.

After wiring the Adjudicator's tool loop, the system worked. Correct decisions, sound rationales, tools being called. But INV-1012 was making **41 tool calls and 17 model calls** to reach a conclusion that needed maybe four lookups — and taking three minutes to do it.

Nothing was throwing errors. The output was right. The cost was quietly 3–4× what it should have been.

The trace showed it: of 41 tool calls, only 12 were unique. `get_vendor_record("FastShip Ltd.")` fired five separate times, returning the identical answer each time. The pipeline ran five agent nodes — adjudicate, critic, revised adjudicate, critic again, revised again — and **each one began its investigation from an empty conversation.** The critic couldn't see what the adjudicator had already learned. The revised adjudicator couldn't see either. Every node rediscovered the same facts from scratch.

Two fixes, addressing different problems:

- A **run-scoped tool cache** so the same lookup can't return two different answers within one invoice's decision — a coherence fix, not just a cost one
- A **prior-investigation summary** passed into every downstream agent's prompt, with an explicit instruction to reuse established facts rather than re-query

The second one did the real work. Cache hits ended up at zero, because once the critic could see what had already been established, it stopped asking again.

Then a second, subtler version of the same problem: critic round 2 was firing on every invoice that had been revised once — but it received the same invoice, the same findings, the same tools, and the same prompt as round 1. Now it only runs one round, unless the second would differ significantly from the first.

Result: INV-1012 from 41 tool calls to 11, 17 model calls to 9, $0.18 to $0.10 — with the same outcome and the same rationale.

A second near-miss, where a fix was itself wrong and got caught by a determinism check before it shipped, is written up in [`docs/eval-results.md`](docs/eval-results.md#the-duplicate-selection-fix--story-of-a-near-miss).

---

## Testing with your own invoices

The provided corpus replays offline. I made it this way so that whoever reviews the build can see its behavior right away, without needing to bill my API every time the same invoices are being processed. However, a **new** invoice has no recorded response, so it needs a live API call.

If you run it without a key first, you'll get an explanation rather than a failure — replay is the default, and live is explicit opt-in:

```bash
.venv/bin/python main.py --invoice_path=/path/to/your/invoice.txt
# → explains why there's no recording and what to do about it
```

To actually process it:

```bash
cp .env.example .env
# add your key: XAI_API_KEY=xai-...
.venv/bin/python main.py --invoice_path=/path/to/your/invoice.txt --live
```

Typically $0.01–0.10 depending on whether it escalates, capped by a per-invoice circuit breaker. Fresh cassettes are recorded so subsequent replays of that same invoice are free.

For example, if you run four new invoices once against your live API, those recordings persist — and the next person can process those same invoices without needing a key at all.

The dashboard also accepts uploads, with the live call gated behind an explicit confirmation and a cost estimate. Nothing else in the dashboard can make an API call — page renders are forced to replay mode so browsing can never bill you.

---

## Assumptions

- **Stock is a point-in-time availability check, not a depleting pool.** Aggregate corpus demand for WidgetA is ~90 units against 15 in stock. Decrementing would fail everything after the first two invoices.
- **Reference unit prices ($250 / $500 / $750) were derived** from the modal price across the corpus. The brief supplies stock levels only.
- **The vendor master is seeded** to reflect a plausible established-manufacturer relationship set. In production it's the ERP vendor master. Four corpus vendors are deliberately absent so the unknown-vendor check has teeth; FastShip Ltd. is present-but-inactive to make INV-1012 genuinely ambiguous.
- **FX is a static constant** with a documented as-of date. Production hits a rate service.
- **`grok-4.5` is an alias**, not a dated identifier — no dated equivalent exists on this account. Every call records the resolved model and `system_fingerprint` so runs stay attributable.

---

## What I cut, and why

- **Three-way matching (invoice ↔ PO ↔ goods receipt).** INV-1012 references `PO-20260115`, and a purchase-orders table would enable a genuine two-way match. Understood and descoped — the exception taxonomy delivers more per hour of build time.
- **Cross-invoice threshold-structuring detection.** INV-1008 and INV-1012 both sit just under $10,000; two authored adversarial invoices from the same vendor three days apart do the same. Each fires `PO-002` individually; nothing recognizes the pair.
- **`AR-003` (tax-rate mismatch) is documented but not implemented.** Phase 4 declined it — inferring a tax rate in order to flag it manufactures findings, and `AR-004` catches the sum. Authoring an invoice specifically to trip it was how the gap got proven by observation rather than assumed.
- **A zero-findings fast path.** A clean invoice still costs ~$0.012 for the Adjudicator to conclude nothing is wrong. Short-circuiting those deterministically would make them nearly free.

---

## Reproducibility

Every LLM call the system has made is committed as a cassette — request fingerprint, full response, token and cost metadata. Replay mode serves them without touching the network.

The cassette key hashes the prompt text, so editing a prompt forces a cache miss rather than silently returning a stale response. That property caught a portability bug where absolute filesystem paths had been baked into recorded requests, which would have broken the demo on any machine but mine.

Replay is deterministic; live mode is not. That's why the recordings are committed — a reviewer gets the same result every time.

- `make demo` — 16 invoices, offline, in about a second
- `make demo-digest-check` — verifies decisions, findings, and costs against a committed baseline
- `make eval` — the scoring harness, exits nonzero on any regression
- `make report` — corpus statistics from the audit store
- `make dashboard` — the web view at `127.0.0.1:8000`

---

## Documentation

| Document | What's in it |
|---|---|
| [`docs/exception-taxonomy.md`](docs/exception-taxonomy.md) | Every finding code: severity, trigger, detection, business rationale, corpus case |
| [`docs/eval-results.md`](docs/eval-results.md) | Full scoring, named misses, business extrapolation, the adversarial set |

### The three build files

Claude Code wrote most of the Python here. It didn't decide what to build, in what order, or to what standard — three files carried that, and they're committed because they're the actual record of how this was directed.

| File | Role |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | The specification. Read automatically at the start of every coding session. Holds the rules that must never drift: the deterministic/LLM boundary, the CRITICAL guardrail, the pinned stack, the domain constants, and a set of corpus facts discovered early so they wouldn't be rediscovered badly later. |
| [`PLAN.md`](PLAN.md) | The phased spine. Thirteen phases with explicit exit criteria, plus a cut list written in advance naming what would be dropped first if time ran short — deciding that before the pressure rather than during it. |
| [`DECISIONS.md`](DECISIONS.md) | Append-only build log, 700+ lines. Three lines per entry: what was decided, what was rejected, why. Written at the moment of each choice rather than reconstructed afterward, because the reasoning behind a reversal evaporates within hours. |

Each phase ran as an explicit prompt with a scope fence — what to build, and an enumerated list of what *not* to build.

`DECISIONS.md` is the one worth opening if you want to see how this was actually reasoned about. It includes the mistakes.
