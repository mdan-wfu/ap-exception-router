# DECISIONS.md

Append-only. Each entry: Decision / Alternatives considered / Why + date.

---

**Decision:** Use `openai` Python package against `base_url="https://api.x.ai/v1"` — not `from xai import Grok`.
**Alternatives considered:** The case brief's `from xai import Grok` import snippet.
**Why:** No such package exists on PyPI. xAI's public API is OpenAI-compatible; the `openai` SDK with a custom base URL is the documented integration path per xAI console docs.
2026-07-29

---

**Decision:** API endpoint is `https://api.x.ai/v1` — not `https://grok.x.ai`.
**Alternatives considered:** The case brief's `https://grok.x.ai` URL.
**Why:** `https://grok.x.ai` is the consumer chat product. The developer API lives at `api.x.ai/v1`. Using the wrong URL returns 404 or redirects; the correct URL is shown in the xAI console quickstart.
2026-07-29

---

**Decision:** Pin model to `grok-4.5`. Available models on this account: `grok-4.20-0309-non-reasoning`, `grok-4.20-0309-reasoning`, `grok-4.20-multi-agent-0309`, `grok-4.3`, `grok-4.5`, `grok-build-0.1`, `grok-imagine-image`, `grok-imagine-image-quality`, `grok-imagine-video`, `grok-imagine-video-1.5`.
**Alternatives considered:** `grok-4.20-0309-non-reasoning` (dated identifier, per CLAUDE.md §3 guidance) and `grok-4.20-0309-reasoning` (reasoning-enabled). Image and video models are out of scope. `grok-build-0.1` is unknown/experimental.
**Why:** `grok-4.5` is the highest version number among chat models on this account. CLAUDE.md prefers dated identifiers, but the only dated identifiers are the March 9 2025 (`-0309`) builds, which appear older than `grok-4.5`. If xAI publishes a dated release of `grok-4.5`, swap to that.
2026-07-29

---

**Decision:** Capability probe — all three checks PASS on `grok-4.5`: basic completion, structured output via `response_format json_schema`, and tool-calling round-trip.
**Alternatives considered:** Fallback to plain JSON mode plus Pydantic repair loop if structured output failed.
**Why:** No fallback required. Structured outputs work without `minLength`/`maxLength`/`minItems`/`maxItems`/`pattern` constraints in the sent schema. `max_completion_tokens` (not `max_tokens`) confirmed working.
2026-07-29

---

**Decision:** Extraction baseline results (minimal prompt, 3 runs × 4 documents, `grok-4.5`):
  1. **Silent OCR correction (INV-1012):** Model corrected `2O26` → `2026` in date and `$3,500.O0` → `$3,500.00` in total across all 3 runs with zero signal. Raw values `date: "26-Jan-2026"` and `total: 9975.0` appear clean — no indication a repair occurred.
  2. **Line-item collapsing (INV-1013):** No collapsing. All 3 runs returned all 8 distinct line items. pdfplumber extracted the table cleanly (intermediate text in `scripts/probe_output/invoice_1013_pdfplumber.txt`). Collapsing risk not realized at default temperature.
  3. **Date hallucination (INV-1003):** Model returned literal string `"yesterday"` for `due_date` — not null, not a fabricated concrete date. No hallucination of a plausible date. Validator still cannot parse the value; `TM-` finding must fire on non-ISO-date strings.
  4. **Normalization drift (INV-1002):** All 3 runs returned `invoice_number: "1002"` (bare number). No run normalized to `"INV-1002"`. Dedupe key logic must normalize invoice numbers deterministically before comparison.
  5. **Run-to-run variance:** None detected on any field across all 4 documents. Temperature need not be reduced before proceeding.
**Alternatives considered:** Tuning the prompt to force null on unparseable dates or to normalize invoice numbers before this measurement.
**Why:** Baseline must be untuned — it measures the raw model, not a prompt under development.
2026-07-29

---

**Decision:** Add `corrections[]` field to the `Invoice` schema (Phase 1).
**Alternatives considered:** Omit it; accept that silent OCR repairs are invisible to the validator layer.
**Why:** INV-1012 baseline confirmed silent correction of `2O26` and `$3,500.O0` with no indication. Without `corrections[]`, the `EX-` finding for OCR repair can never be raised — the validator sees a clean value and has no evidence a substitution occurred. The extractor prompt must populate `corrections[]` whenever it repairs a value; validators emit `EX-001` on non-empty corrections.
2026-07-29

---

**Decision:** OCR corruption in INV-1012 is deliberately injected as Python string literals in `generate_pdfs.py:create_messy_invoice`. Exact substitution rule: capital letter `O` for digit `0`, appearing in two specific tokens: `"26-Jan-2O26"` (date) and `"$3,500.O0"` (currency line amount). O→0 normalization must be **narrow** — apply only within tokens that are otherwise numeric/currency (digits, `$`, `,`, `.`).
**Alternatives considered:** Global O→0 substitution across every extracted string.
**Why:** The rest of INV-1012 contains legitimate capital `O` characters (`INVOICE`, `INV NO`, `TOTAL`, `SUBTOTAL`, `NOTES`, `Contact`, `Payble`). Global substitution would corrupt these. Two data points is enough to establish the rule when the same character never fires in the letter tokens.
2026-07-29

---

**Decision:** The txt/pdf duplicate pairs (INV-1011, INV-1012, INV-1013) are deliberate. `generate_pdfs.py` renders exactly three PDFs — no others — and each of the three has a companion source file (`.txt` for 1011/1012, `.json` for 1013). No other invoice in the corpus has both a text-form source and a rendered PDF.
**Alternatives considered:** Treating the pairs as rendering artifacts to be normalized.
**Why:** The generator selectively renders three invoices to create the duplicate-detection scenario. It is not a global "render everything" pass. Dedupe logic must treat the pairs as intentional traps for `DP-` findings.
2026-07-29

---

**Decision:** `generate_pdfs.py` embeds all three source invoices as Python literals; ground truth extracted verbatim to `eval/source_literals.py` (marked EVAL-ONLY). Enforced by `tests/test_integrity.py`.
**Alternatives considered:** Deriving ground truth by hand from the rendered PDFs.
**Why:** Hand-derivation reintroduces error in the ground truth. Verbatim literal extraction is authoritative. The integrity test grep prevents `src/` from ever importing `eval/`, without which extraction-accuracy numbers would be circular.
2026-07-29

---

**Decision:** Contradiction with CLAUDE.md §6: INV-1013 also has a **deliberate arithmetic error** on grand total, not just the aggregate-stock overrun that §6 documents. `generate_pdfs.py` renders `f"${grand_total + 50:,.2f}"` — showing $22,562.80 where the computed subtotal+tax is $22,512.80. The JSON companion (`invoice_1013.json`) carries the same $50 offset in its `total` field. AR- findings on INV-1013 are expected, and the JSON adapter must not silently trust `total` as the source of truth.
**Alternatives considered:** Ignoring the discrepancy since §6 doesn't mention it.
**Why:** The offset is present in both the PDF renderer and the JSON literal — this is not a scripting bug, it is a designed trap. `docs/exception-taxonomy.md` (Phase 7) will need to reflect this; the ground truth in `eval/source_literals.py` records `grand_total_computed` (22512.80) and `grand_total_rendered` (22562.80) separately.
2026-07-29

---

**Decision:** Use `Decimal` for every monetary value in the schema; never `float`. Pydantic v2 configured to coerce string or number input into `Decimal`.
**Alternatives considered:** `float` throughout (idiomatic Python) with epsilon comparisons in the arithmetic validator.
**Why:** Cent-level equality on floats produces phantom `AR-` findings. `Decimal("9500.00") + Decimal("475.00") == Decimal("9975.00")` is exact; the `float` equivalent depends on how the values arrived. The arithmetic validator's whole job is exact comparison — floats would defeat it.
2026-07-29

---

**Decision:** Evidence-preservation principle for the `Invoice` schema. The Phase 0b probe used `vendor: str` and consequently dropped `(formerly FastShip Ltd.)`, the `PO-20260115` reference, and the notes block from INV-1012. Extraction can only preserve what the schema has a slot for. Fields added specifically to prevent this evidence loss: `invoice_number_raw`, `invoice_number` (normalized), `vendor_raw`, `vendor_name`, `vendor_claims: list[str]`, `vendor_address`, `vendor_email`, `references: list[str]`, `notes`, `additional_charges: list[AdditionalCharge]`, `line_items[].raw_item_name` (in addition to `canonical_item`).
**Alternatives considered:** A minimal schema matching the case brief's implied shape, with claims and references detected downstream from raw text.
**Why:** VN-004 (INV-1012's flagship escalation finding) depends on `vendor_claims`. Additional-charges as a separate list keeps INV-1010's $150 shipping from being silently rolled into the subtotal. Fraud signals (`FR-*`) will scan raw source text via `source_file`, not the extracted `Invoice`, since urgency language ("URGENT — Pay immediately") is exactly the kind of evidence extraction drops.
2026-07-29

---

**Decision:** Invoice-number normalization rule: uppercase, strip non-alphanumerics, take the trailing digit run, emit `INV-{digits}`. Implemented in code (`src/store/canonical.normalize_invoice_number`), not the extractor prompt. The `INV-1004` collision between `invoice_1004.json` and `invoice_1004_revised.json` is intended — it is the dedupe key for the flagship escalation case.
**Alternatives considered:** Ask the model to normalize during extraction (Phase 0b showed it returns bare `"1002"`).
**Why:** Deterministic normalization means the dedupe key is stable across every code path. Model-driven normalization is inconsistent (baseline confirmed) and would silently defeat duplicate detection. Preserving `invoice_number_raw` retains the original as evidence.
2026-07-29

---

**Decision:** Fuzzy matching is used for vendors only, never for items. `canonicalize_item` does an exact match against the inventory catalog and returns `None` for anything else.
**Alternatives considered:** Fuzzy item matching to catch `WidgetC` → `WidgetA`.
**Why:** A wrong item mapping produces a silently wrong stock check and a silently wrong price comparison. `IN-001` "unknown item" is a legitimate finding for the Adjudicator to resolve; a silent misroute is invisible.
2026-07-29

---

**Decision:** Model resolution captured on every LLM call (`ModelCall.resolved_model` in the `RunRecord`).
**Alternatives considered:** Record only the requested model identifier.
**Why:** `grok-4.5` is an alias with no dated equivalent on this account. Aliases can shift underneath a build. Capturing `response.model` on every call means the audit trail proves what actually answered, even if the alias moves between Day 1 and Day 3.
2026-07-29

---

**Decision:** Split `Invoice.content_hash` into two fields: `file_hash` (SHA-256 of raw bytes, unchanged) and `semantic_hash` (SHA-256 over normalized invoice_number, normalized vendor_name, sorted (item, quantity, unit_price_usd) tuples, and stated_total_usd). Semantic hash deliberately excludes subtotal, tax, notes, references, and source format.
**Alternatives considered:** Retain a single `content_hash` over the full canonical `Invoice`, or over all monetary fields including subtotal and tax.
**Why:** `file_hash` fires zero times on this corpus because a txt invoice and its rendered PDF are never byte-identical. INV-1011's PDF omits the subtotal and tax lines its txt source contains, so any hash covering those fields would classify the pair as DP-002 (same number, differing content) when the correct classification is DP-001 (same invoice, two files). The semantic core must exclude rendering-dependent fields. INV-1004 vs INV-1004_revised still separate correctly on `semantic_hash` because their line items and totals genuinely differ.
2026-07-29

---

**Decision:** Cassette key is a SHA-256 over: model identifier + full serialized messages (system + user) + tool definitions + response-format schema. `max_completion_tokens` is deliberately excluded.
**Alternatives considered:** Key on (model, first message, tool names) only; key on request-body hash including all sampling params.
**Why:** Prompt text MUST be in the key. Editing `prompts/extractor.md` changes the messages, which changes the key, which forces a cache miss. Without this rule you spend hours debugging changes that never took effect. `max_completion_tokens` is excluded because raising the budget for a recorded response should still hit — the actual content did not change.
2026-07-29

---

**Decision:** `LLM_MODE` defaults to `auto` (replay on hit, live on miss, record the result). `make demo` sets `LLM_MODE=replay` and passes `--replay` so a cold clone with no API key runs to completion with a loud error if a cassette is missing.
**Alternatives considered:** Default to `replay` (safer, no accidental API spend) or `live` (fewer surprises during dev).
**Why:** `auto` is the only mode that keeps the dev loop cheap (cassettes are hit most of the time) while remaining tolerant of new prompts (misses record automatically). CI/reviewer runs use `make demo`, which forces `replay` — a missing cassette on that path is a build failure, not an unexpected charge.
2026-07-29

---

**Decision:** `PRICE_PER_1M_INPUT` and `PRICE_PER_1M_OUTPUT` are placeholders (0.0) with a `TODO(pricing)` comment in `src/config.py`.
**Alternatives considered:** Guess reasonable numbers (e.g., $2/$10 per 1M).
**Why:** Every `RunRecord.cost_usd` derives from these two constants. Inventing them means every Day-2 cost claim is fiction. Placeholders that report $0 are obviously wrong; guessed numbers are subtly wrong. Confirm from the xAI console before Phase 8 (observability). Recorded first-run smoke `pong` cassette shows `cost_usd: "0E-7"` — the placeholder is visibly firing.
2026-07-29

---

**Decision:** `ModelCall.resolved_model` capture confirms `grok-4.5` currently resolves to `"grok-4.5"` on this account (visible in every recorded cassette). No dated alias exists.
**Alternatives considered:** Trust that the alias is stable and drop the capture.
**Why:** The capture is exactly the audit trail we need to detect a silent alias swap. If xAI later remaps `grok-4.5` to a newer or older build, the run history will show it, and Day-3 eval numbers can be compared against Day-1 numbers without ambiguity about what actually ran.
2026-07-29

---

**Decision:** Cassette redaction — refuse to `put()` any payload containing `xai-[A-Za-z0-9_-]{6,}`. A committed test (`test_committed_cassettes_contain_no_credentials`) also greps the entire `data/cassettes/` directory for the same pattern and fails if anything matches.
**Alternatives considered:** Trust that the SDK never echoes credentials into responses.
**Why:** Defense in depth. The request payload includes user-provided message content, which could plausibly contain a leaked key at any point (someone pasting a `.env` snippet into an invoice PDF for testing). The check runs on write AND at CI, so a leak in either the recording path or a manually-edited cassette fails loudly.
2026-07-29

---

**Decision (correcting a prior entry):** The earlier Phase 2 claim that `resolved_model` capture "will detect any silent swap" is wrong. `response.model` echoes the alias verbatim on xAI (`grok-4.5` → `grok-4.5`), so if xAI repointed the alias to different weights, that field would show no change. Adding `ModelCall.system_fingerprint`, populated from `response.system_fingerprint` when the provider supplies it.
**Alternatives considered:** Trust the earlier (wrong) claim and skip fingerprint capture; try to detect drift by comparing response quality across runs.
**Why:** `system_fingerprint` is the only field a provider exposes that would actually shift when the backend build changes. Empirical probe on 2026-07-29 confirms xAI populates it (example value: `fp_a39489019fa99b6e`). `resolved_model` remains useful — it proves what was requested and acknowledged — but it does NOT prove what actually served. If a future provider omits `system_fingerprint`, alias drift is undetectable on that provider and is an accepted known risk; note it in the RunRecord audit view and document as a limitation.
2026-07-29

---

**Decision:** Freeze `Invoice`, `LineItem`, `Money`, `AdditionalCharge`, and `Correction` (Pydantic `frozen=True`). Callers who need a modified invoice use `model_copy(update=...)`, which triggers the semantic-hash validator on the copy.
**Alternatives considered:** Keep the models mutable and rely on discipline to not mutate.
**Why:** `Invoice.semantic_hash` is computed once at construction. A mutable `Invoice` — or a mutable nested `LineItem` — allows `invoice.line_items[0].quantity = 999` to silently change the semantic identity while the cached hash keeps the old value. Freezing eliminates the drift by construction rather than by convention.
2026-07-29

---

**Decision:** `ModelCall` now captures four token categories, not two: `prompt_tokens`, `cached_prompt_tokens`, `completion_tokens`, `reasoning_tokens`. Populated from `response.usage.prompt_tokens_details.cached_tokens` and `response.usage.completion_tokens_details.reasoning_tokens` respectively.
**Alternatives considered:** Keep the two-field `tokens_in`/`tokens_out` shape and eat the granularity loss.
**Why:** `grok-4.5` is a reasoning model. Console billing for this account shows reasoning tokens as the single largest cost line (4.7K reasoning vs 2.6K completion in one session). A two-field capture cannot express this and every cost figure downstream would be materially wrong.
2026-07-29

---

**Decision:** Empirically confirmed on 2026-07-29 that `completion_tokens` does NOT include `reasoning_tokens` on xAI grok-4.5. Verified by inspecting `response.usage`: `total_tokens (233) == prompt_tokens (209) + completion_tokens (1) + reasoning_tokens (23)`. The cost formula therefore charges the output rate against `completion_tokens + reasoning_tokens`.
**Alternatives considered:** Assume completion includes reasoning (a common convention) without verifying.
**Why:** If we assumed inclusion and it's actually disjoint, we'd underreport cost by the reasoning fraction — which is the dominant term. If we assumed disjoint and it's actually inclusion, we'd double-count and every cost figure would inflate. The empirical check was one call; skipping it would put every subsequent number at risk.
2026-07-29

---

**Decision:** Pricing constants in `src/config.py` set from console billing on 2026-07-29: `PRICE_PER_1M_INPUT = 2.00`, `PRICE_PER_1M_CACHED_INPUT = 0.50` (TODO: confirm; materially below 2.00), `PRICE_PER_1M_OUTPUT = 6.00`. Confirm against xAI's published pricing page before Phase 8.
**Alternatives considered:** Keep the zero placeholders from the earlier Phase 2 entry.
**Why:** Zero placeholders make every cost figure a lie by omission and would defeat the Day-2 observability rollup. Console-derived numbers are approximate but recoverable; the placeholder-only run couldn't be reconciled later.
2026-07-29

---

**Decision:** `ModelCall.cost_usd` is a `@computed_field` derived at read time from `src.config` pricing. Cassettes store token counts only; `cost_usd` is excluded from `model_dump_json()` at write time.
**Alternatives considered:** Freeze `cost_usd` into every cassette so the recorded number is exactly what was paid.
**Why:** Cost is derived from tokens and pricing, not observed independently. Freezing it into cassettes means any later pricing correction silently invalidates every prior recording, and a demo/eval run replayed after a price update reports figures nobody can reconcile against the current rate card. Tokens are the observed quantity; cost is a calculation over them.
2026-07-29

---

**Decision (correcting a prior entry):** The prior `PRICE_PER_1M_CACHED_INPUT = 0.50` was a guess. Replaced with `PRICE_PER_1M_CACHED_INPUT = PRICE_PER_1M_INPUT` (currently $2.00/M) as a conservative stand-in until the real cached rate is confirmed from the xAI console.
**Alternatives considered:** Keep the $0.50 guess; set to $0 (undercount); leave the placeholder unresolved.
**Why:** Cached tokens are always billed at or below the uncached rate in practice, so charging the uncached rate can only OVERstate cost — never understate. The prior $0.50 could go either way (over or under) depending on the real number, and any Day-2 cost claim built on it would be unfalsifiable. A stand-in that biases toward overstating cost is defensible: the reported figure is an upper bound. Replace with the confirmed rate before Phase 8 and update the corresponding test.
2026-07-29

---

**Decision:** Extraction is permissive by design. `Invoice` accepts empty vendor names, negative quantities, null due dates, negative totals, and stated subtotals that contradict the line items. Structural validity only; nothing in the schema evaluates business rules.
**Alternatives considered:** Reject with Pydantic constraints (`PositiveInt`, non-empty strings, `Field(ge=0)`).
**Why:** INV-1009 has quantity=-5 and empty vendor; INV-1013 carries a $50 grand-total error. If Pydantic rejects any of these, extraction fails, the repair loop tries to "correct" the document into validity, and the Phase 4 finding never fires. A schema that enforces business rules destroys the evidence those rules exist to detect.
2026-07-29

---

**Decision:** `corrections[]` is reserved for changes that alter what the source LITERALLY said. Date format normalization (`01/28/2026` → `2026-01-28`, `26-Jan-2026` → `2026-01-26`) is a representation change and does NOT go in `corrections[]`. OCR character substitutions (letter `O` → digit `0`) DO.
**Alternatives considered:** Log every parse into `corrections[]` for symmetry.
**Why:** `corrections[]` is the evidence trail for `EX-` findings — any entry there should signal "the extractor decided the source was wrong and repaired it." Logging every reformat pollutes that signal to the point of uselessness. A downstream reader of `corrections[]` should read every entry as noteworthy.
2026-07-29

---

**Decision:** CSV shape detection uses header width. `field,value` (2 cols, first cell = "field") → vertical key–value; anything else → row-per-item. Vertical shape is parsed positionally with an accumulating item dict, never `csv.DictReader`.
**Alternatives considered:** Sniff dialect + fall through both parsers; require an explicit hint per file.
**Why:** `csv.DictReader` collapses repeated keys silently — INV-1006 has `item` twice, and a naive dict would drop the first line item without any error. The two shapes in this corpus are structurally distinct enough that a 2-line-of-code detector is unambiguous; a more general sniffer would be more code for the same guarantee.
2026-07-29

---

**Decision:** The extractor prompt succeeded in making silent repairs declared. Phase 0b baseline (minimal prompt): INV-1012 returned `date: "26-Jan-2026"` and `total: 9975.0` with `corrections: []`. Phase 3 (prompts/extractor.md, replayed cassette): INV-1012 returns the same corrected values PLUS `corrections=[{field_path: "invoice_date", original: "2O26", corrected: "2026", reason: "OCR: letter O replaced by digit 0 in date token"}, {field_path: "line_items[1].line_amount", original: "$3,500.O0", corrected: "$3,500.00", reason: "OCR: letter O replaced by digit 0 in currency token"}]`, plus `vendor_claims=["(formerly FastShip Ltd.)"]`, plus `references=["PO-20260115"]`, plus the notes block. Every piece of evidence the minimal-prompt run silently dropped now lands in the extracted Invoice.
**Alternatives considered:** Trust the model's raw output and detect repairs by comparing extracted values to source text via a diff.
**Why:** A post-hoc diff cannot distinguish an intentional character-level repair (`2O26` → `2026`) from a coincidental match on a different token. Making the model declare its repairs at extraction time keeps the evidence attached to the field being repaired and to a human-readable reason.
2026-07-29

---

**Decision:** No file required LLM fallback. All six JSON, three CSV, and one XML file cleared `MIN_FIELD_COVERAGE=0.75` on the deterministic parse and never triggered `_fall_back()`. INV-1009 (empty vendor) sits at exactly 3/4 coverage — the threshold is deliberately at that edge; drop it and INV-1009 would silently degrade to an LLM extraction that would probably invent a vendor name.
**Alternatives considered:** Raise threshold to 1.0 (all four core fields must be present); lower to 0.5.
**Why:** 0.75 catches structurally broken files (three or more of the four core fields missing) while accepting the corpus's known-permissive edge case. If a future corpus needs a stricter bar, tighten per-adapter — a global bump would pull INV-1009 into LLM territory.
2026-07-29

---

**Decision:** Canonical monetary field names on `Invoice` are `stated_subtotal`, `stated_tax`, `stated_total` — all three carrying the `stated_` prefix. Phase 4 prompts and validators reference exactly these names.
**Alternatives considered:** Drop the prefix (`subtotal`/`tax`/`total`); use `document_subtotal`/etc.
**Why:** The prefix signals "what the document CLAIMS", distinguishing these from any recomputed totals Phase 4 will produce. Consistent prefixing across all three prevents the class of bug where one field is prefixed and the others aren't, which invites accidental comparison of a stated value against a computed one under a similar name.
2026-07-29

---

**Decision (correcting a prior entry):** Coverage-based LLM fallback removed from the router. Structured formats (json/csv/xml) fall back to the LLM ONLY on a parse exception (json.JSONDecodeError, csv.Error, xml.etree.ElementTree.ParseError, pydantic.ValidationError). Field coverage no longer triggers fallback for any adapter. `MIN_FIELD_COVERAGE` and `field_coverage()` deleted as unused.
**Alternatives considered:** Keep coverage-based fallback but tighten the threshold; apply coverage only to LLM-native adapters.
**Why:** For structured formats, a successful parse is authoritative. Low coverage means the SOURCE is sparse (as INV-1009 deliberately is), not that extraction failed. Falling back to the LLM on a correctly-parsed sparse document risks fabricating values the source legitimately omitted — exactly what prompts/extractor.md forbids ("Never fabricate a value. When a field is missing... return null."). LLM-native adapters (text/pdf) never fall back because they already are the LLM. The prior entry noting "no file required LLM fallback because INV-1009 sits at 3/4" was correct as far as it went, but hid a latent risk: an adversarial invoice at 2/4 or 1/4 coverage would have silently degraded a valid deterministic parse into a hallucination-prone LLM extraction. That risk was one field short of tripping on this corpus and would have shipped as a bug into Phase 10.
2026-07-29

---

**Decision:** `Invoice.extraction_confidence` and `ExtractedInvoice.extraction_confidence` default to `None`, not `1.0`. Deterministic adapters (json/csv/xml) set `extraction_confidence=1.0` explicitly; the LLM adapter carries through whatever the model self-reports, and `None` if the model omits the field.
**Alternatives considered:** Keep default `1.0`; validate that the model always reports a value; drop the field entirely.
**Why:** A missing value silently defaulting to `1.0` means the model asserting maximum confidence precisely when it reported nothing — the worst-possible dishonesty in a system whose whole point is escalating uncertainty. `None` is the honest representation. Deterministic 1.0 is defensible because a structured parse either succeeds exactly or raises. The two 1.0s (deterministic vs LLM self-report) are NOT the same unit and must not be compared as though they were — the Phase 11 dashboard should label them distinctly (e.g. "parsed" vs "self-reported").
2026-07-29

---

**Decision:** `extraction_confidence` is recorded on every Invoice but never gates any downstream behaviour. `_common.field_coverage()` was deleted with the fallback-scope fix and there is no upstream confidence threshold.
**Alternatives considered:** Reject or fall-back on low confidence; add a confidence-based severity boost in Phase 4.
**Why:** LLM extraction quality is detected downstream by validator findings — an extraction that hallucinated a total will trip an `AR-` (arithmetic mismatch) finding when the recomputed total differs; one that missed a vendor trips `VN-005` (missing name). These are more specific and more actionable than any upstream coverage or confidence threshold could be. A finding says "the total in the document does not match the sum of its line items"; a low confidence score says "I feel bad about this one." Only the finding is investigable.
2026-07-29

---

**Decision:** Fuzzy vendor threshold: `difflib.SequenceMatcher` ratio ≥ 0.70 on lowercased vendor names, plus a preceding claim-based exact-substring check that looks for any master vendor name inside `vendor_claims`.
**Alternatives considered:** rapidfuzz's token-set ratio (adds a dependency), threshold 0.60 or 0.80.
**Why:** Calibrated against the deliberate false-positive: `Acme Industrial Supplies` (INV-1006) shares the token `Acme` with the buyer `Acme Corp`. SequenceMatcher gives that pair ~0.42 — safely below 0.70 in either direction. INV-1012's flagship signal — `QuickShip Distributers` claiming `(formerly FastShip Ltd.)` — is caught by the claim-based path (FastShip Ltd. is a master vendor name that appears as a substring inside the claim), not by fuzzy on the raw vendor name (`quickship distributers` vs `fastship ltd.` scores ~0.30, well below 0.70). Both paths must exist; each catches a distinct class of case.
2026-07-29

---

**Decision:** `AR-003` (tax mismatch as an independent check) is intentionally NOT implemented. Tax is only checked as a component of the grand-total sum (`AR-004`). Where the source states a tax amount without a rate — INV-1010 states `Sales Tax: $335.00` with no percentage — we do not infer a rate and then flag the result.
**Alternatives considered:** Infer the tax rate as `stated_tax / stated_subtotal` and flag when it lands on a suspicious value (e.g. non-multiple of 0.5%).
**Why:** Inferring a rate to then flag it is a manufactured finding. The invoice claims a subtotal, a tax, and a total; the grand-total check (AR-004) already fails if `subtotal + tax + extras ≠ total`. Adding an inferred-rate check would fire on every legitimate invoice with a non-round tax amount and dilute the AR- signal.
2026-07-29

---

**Decision:** DP-001 completeness preference: choose the record with more non-null fields among `stated_subtotal`, `stated_tax`, `payment_terms`, `vendor_address`; tie-break on largest `len(line_items)`. Preferred record and reason logged in the DP-001 evidence.
**Alternatives considered:** Prefer .pdf over .txt (renders are canonical); prefer the file with more raw bytes; prefer alphabetically-first source_file.
**Why:** INV-1011's PDF is materially LESS complete than its txt source (no subtotal/tax lines rendered). Preferring PDF would systematically lose fields for this pair. Byte-count and alphabetical fall out arbitrarily. Non-null-field count reflects what the AP clerk actually cares about — how many fields are filled. Line-item count breaks ties without introducing a format bias.
2026-07-29

---

**Decision:** Discovery — INV-1007 (row-per-item CSV) has an undocumented arithmetic error. Subtotal $14,750 + Tax (6%) $885 = $15,635, but the file states Total $15,525 — a $110 discrepancy. CLAUDE.md §6 does not call this out.
**Alternatives considered:** Treat it as a bug in the corpus and suppress the finding; adjust AR-004 tolerance to hide it.
**Why:** The file literally states inconsistent numbers. AR-004 correctly fires. Suppressing the finding to match CLAUDE.md's expectation would be silently accommodating a documentation gap. Recording this here means the ground-truth table in Phase 9 will list INV-1007 as an AR-004 case, and if the corpus author later confirms the trap was intentional (as with INV-1013's $50), we already have the citation.
2026-07-29

---

**Decision:** IN-003 (aggregate exceeds stock) does NOT fire for inactive items (only IN-002 does). INV-1003's `FakeItem` qty 100 vs stock 0 produces IN-002 ("inactive with zero stock"), not IN-002 + IN-003.
**Alternatives considered:** Fire both — IN-002 for the inactive status and IN-003 for the demand overrun.
**Why:** For an inactive item, the correct message is "this SKU is dead"; the demand vs standing-stock comparison is a category error (stock is 0 by definition for inactive). The signal is not "you asked for more than we have" — it is "you can't order this at all." IN-002 already carries CRITICAL-adjacent weight; adding IN-003 duplicates without adding evidence.
2026-07-29

---

**Decision:** Severity baseline calibration (Phase 4 initial; revisit in Phase 7 with the eval harness):
  - CRITICAL: `AR-005` (negative qty), `AR-006` (negative total), `VN-005` (empty vendor), `DP-002` (differing content under same number).
  - HIGH: `AR-002`/`AR-004` (subtotal/grand-total mismatch), `IN-001`/`IN-002`/`IN-003`, `PR-001` (over reference), `VN-001`/`VN-003`(master)/`VN-004`, `TM-003`, `DP-003`, `PO-001`.
  - MEDIUM: `AR-001` (single-line mismatch), `AR-003` (never fires), `PR-003`, `VN-002`, `VN-003`(unknown-vendor variant), `TM-001`, `DP-001`, `PO-002`, `FR-002`, `FR-003`.
  - LOW: `PR-002` (under reference), `PR-004` (no reference), `TM-002`, `FR-001`.
  - INFO: `EX-001`, `PO-003` (FX applied).
**Alternatives considered:** Push more items to CRITICAL to force auto-rejects; keep everything MEDIUM to defer all decisions.
**Why:** CRITICAL is reserved for "cannot pay under any reading" — arithmetic that can't be reconciled (negative), the party that can't be identified (empty vendor), and the case where paying twice is a live risk (DP-002). Everything else is judgment; the Adjudicator in Phase 5 aggregates. Findings first, severity next — the finding set is right and this calibration is a deliberate starting point, not a fit to the 5/7/5 target.
2026-07-29

---

**Decision:** Disagreement with CLAUDE.md §6 (recorded, not silently accommodated): §6 documents INV-1013's aggregate-stock overrun and the +$50 grand-total error together, but does not name INV-1007's $110 grand-total error at all. My reading: INV-1007 is a genuine additional trap the current §6 text omits; AR-004 correctly fires on it. If future revisions of the case brief clarify that INV-1007 was intended to be arithmetically clean, the fix is in the corpus, not the validator.
**Alternatives considered:** Read §6's silence as normative and suppress AR-004 for INV-1007.
**Why:** Every dollar in an AP system is either right or wrong. The invoice's own arithmetic is wrong by $110. Adjusting the validator to accommodate a documentation gap trains the system to hide arithmetic errors when the doc doesn't warn about them — the exact opposite of the phase's contract.
2026-07-29

---

**Decision (correcting a prior entry):** `DP-001` severity downgraded from MEDIUM to INFO. Also, the finding message now records which file was RETAINED and the completeness reason.
**Alternatives considered:** Keep at MEDIUM; drop DP-001 entirely once a file is chosen; escalate to HIGH so a human always confirms the pairing.
**Why:** DP-001 means the deduplicator successfully identified two files as one invoice and picked the more complete record. That is the system operating correctly — it does not require human attention. At MEDIUM, INV-1011 (whose ONLY finding is DP-001) would push toward ESCALATE when it should cleanly APPROVE. MEDIUM+ is reserved for duplicates the system could NOT resolve, which is DP-002 (already CRITICAL). Retaining the finding at INFO keeps the audit trail (which file was retained, why) without polluting the exception queue.
2026-07-29

---

**Decision:** Corpus arithmetic record (exhaustively verified across all 20 files after INV-1007 revealed that a partial verification had missed a second grand-total error):

  - **INV-1013**: grand-total error of **+$50.00** (overcharge). Subtotal $21,040 + Tax 7% $1,472.80 = $22,512.80, stated total $22,562.80. **Documented** in `generate_pdfs.py` (`grand_total + 50`) and mirrored in `invoice_1013.json`.
  - **INV-1007**: grand-total error of **−$110.00** (undercharge). Subtotal $14,750 + Tax 6% $885 = $15,635, stated total $15,525. **Undocumented** in CLAUDE.md §6, discovered by AR-004.
  - **INV-1009**: subtotal error of **+$1,250** (stated $1,000 against line items summing to −$250 due to `quantity=-5` on WidgetA). The downstream grand-total delta of −$1,250 is a consequence of the same underlying negative-quantity issue and fires AR-004 as well, but the root cause is the subtotal (AR-002).

All other 17 files reconcile exactly on both the line-sum-vs-stated-subtotal step and the (subtotal + tax + additional_charges) vs stated_total step. INV-1014 reconciles exactly in USD after FX: EUR-native totals × 1.14 match stated USD totals to the cent. Verification script: transient one-off; the invariant is locked by the AR-002 / AR-004 tests plus the negative assertion `test_ar_004_does_not_fire_on_clean_inv_1001` and `test_ar_004_does_not_fire_on_inv_1010_because_shipping_is_included`.

**Alternatives considered:** Trust the initial partial check that found only INV-1013 and INV-1009.
**Why:** The initial pass missed INV-1007 because it was not on any hand-curated list. Deriving the answer from the validator against every file avoids the same trap next time.
2026-07-29

---

**Decision:** LangGraph state uses `Annotated[list[Finding], operator.add]` and `Annotated[list[str], operator.add]` reducers on `findings` and `nodes_fired`. Every other field replaces on write.
**Alternatives considered:** Manage findings imperatively (each node reads existing findings and returns the merged list); use `add_messages` from langgraph.
**Why:** Multiple nodes contribute findings (batch pre-pass seeds duplicates; `validate` adds per-invoice checks; Phase 5c agents may add extraction-repair or critic notes). Imperative merge is a footgun — the first node that forgets to include the existing list clobbers everything upstream. The reducer makes accumulation the default and unavoidable.
2026-07-30

---

**Decision:** Collapsed the planned `triage` and `extract` nodes into a single `triage` node. `router.extract()` already returns a finished `Invoice` inside its `ExtractionResult`, and EX- findings are emitted by the extraction validator inside `validate`. A separate `extract` node would be empty apart from a pass-through of state.
**Alternatives considered:** Keep both nodes for symmetry with the phase-diagram sketch.
**Why:** CLAUDE.md §7 says `src/graph.py` "should read like the flow diagram." An empty pass-through node makes the diagram lie: readers expect substantive work at each labelled step. Collapsing keeps the graph honest.
2026-07-30

---

**Decision:** Duplicates run as a pre-pass over the whole batch in `src/batch.py`; per-invoice graph runs are seeded with their own duplicate findings via the `findings` reducer.
**Alternatives considered:** Duplicate check inside the graph via a batch-aware node; post-pass that re-processes results.
**Why:** `find_duplicates(list[Invoice])` needs the whole set to group by normalized invoice_number. A per-invoice graph pass has no view of the set. Making duplicate detection a pre-pass keeps the graph strictly per-invoice while surfacing the finding through the same reducer that per-invoice validators use.
**Known limitation:** `--invoice_path` cannot detect duplicates without corpus context. `main.py` prints `(single-invoice mode: duplicate detection skipped — batch required)` after the result; the Phase 6 audit view will carry the same label. If a user needs duplicate detection for a single new invoice, they run `--batch` — the pre-pass ingests every file including the new one.
2026-07-30

---

**Decision:** Checkpointer is `langgraph.checkpoint.sqlite.SqliteSaver` at `runs/checkpoints.sqlite`, installed at graph compile time. Tests use `build_graph(checkpointer_path=None)` (no persistence) so test invocations do not accumulate state between runs.
**Alternatives considered:** `InMemorySaver` (loses state between processes; Phase 6's human gate needs persistence); Postgres saver (overkill).
**Why:** Phase 6 wires an `interrupt()` human gate; that requires a checkpointer that survives process restarts. SqliteSaver is the smallest viable persistence layer and matches CLAUDE.md §3's pin. Installing it in 5a means Phase 6 does not retrofit the graph.
2026-07-30

---

**Decision:** Extracted `score_candidates(name, reference, min_score, top_n)` in `src/validators/vendor.py`. The validator calls it with the strict `FUZZY_THRESHOLD=0.70`; the tool `get_vendor_record` calls it with an investigative `TOOL_FUZZY_THRESHOLD=0.30` and `top_n=5`.
**Alternatives considered:** Second SequenceMatcher wrapper in `src/tools/`; single shared threshold at either extreme.
**Why:** CLAUDE.md §2.1a mandates one scoring implementation — "Reuse the fuzzy matcher from validators/vendor.py; do not write a second implementation." But the thresholds serve different purposes. The validator's job is to fire VN-002 only when the pair is truly close (a rename, a typo). The tool's job is to surface neighborhood context for the Adjudicator to weigh. `QuickShip Distributers` vs `FastShip Ltd.` scores 0.34 — below the validator threshold, above the tool threshold. Same matcher, different call sites, different jobs.
2026-07-30

---

**Decision:** Tools return well-formed "not found" results on missing vendors, items, invoice numbers, or policy codes. They never raise for absence.
**Alternatives considered:** Raise a typed `NotFoundError`; return `None`.
**Why:** Per CLAUDE.md §2.1a, a tool call inside an agent turn that raises aborts the model's reasoning mid-conversation. A clean not-found lets the model incorporate the absence as evidence — which is frequently the point. INV-1008's vendor being absent from the master IS the finding. Genuine infrastructure failures (DB corruption, missing table) do raise, so an "unreachable audit store" is not silently swallowed.
2026-07-30

---

**Decision:** `get_policy` does NOT return the finding's severity, only its trigger, detection method, rationale, and corpus examples. Severity is returned OMITTED even though the taxonomy documents it.
**Alternatives considered:** Include severity for parity with the taxonomy row.
**Why:** Severity is a documented judgment about how bad a finding class is. The Finding object itself carries its severity to the Adjudicator — the tool doesn't need to re-assert it. Returning severity here would blur the fact/judgment boundary: the tool's job is to explain what a code MEANS, not how bad it is. The Adjudicator forms its own opinion of what to do with a HIGH finding based on the specific evidence.
2026-07-30

---

**Decision:** The fact/judgment line was mildly ambiguous in two places, resolved as follows:
  1. `VendorMasterRow.status: "active" | "inactive"` — kept. `status` is factual master data, not a trustworthiness verdict. `inactive` in the seed data is the DB's ground truth, not the tool's opinion.
  2. `VendorHistoryResult.prior_outcomes: dict[str, int]` — kept. Historical outcomes ARE facts (they're what previously happened). The tool reports what the audit store contains; it does not recommend that the current invoice should share the same outcome.
Recorded here so a future reviewer sees the reasoning rather than second-guessing.
2026-07-30

---

**Decision:** `VendorRecordResult` now carries a top-level `match_threshold: float` and each `VendorFuzzyCandidate` carries a `below_threshold: bool`. Candidates are NOT filtered — neighborhood context stays visible — but their weakness is made legible.
**Alternatives considered:** Return only above-threshold candidates; return scores without any reference number.
**Why:** For `QuickShip Distributers`, FastShip Ltd. (0.343) and Acme Industrial Supplies (0.348) both score far below the VN-002 threshold of 0.70. Presented as a bare ranked list, that implies meaningful ordering between two indistinguishable noise scores AND puts an irrelevant vendor first for the corpus's flagship escalation case. With the threshold surfaced and per-candidate below_threshold flag, a model reading the result can tell 0.348 is neighborhood noise, not a name match.

**Fuzzy name similarity is NOT the signal that resolves INV-1012.** The signal is the explicit `vendor_claims` entry `(formerly FastShip Ltd.)`, whose substring exactly matches the master name `FastShip Ltd.`. That exact-match check is what fires VN-004 in the validator, and it is the substring-hit path in `get_vendor_record` (which surfaces FastShip at score 1.0 when the caller passes the raw vendor string containing the claim). The fuzzy candidate list is corroborating context only — it must not be presented as though it carries the finding, and downstream prompts should read it that way.
2026-07-30

---

**Decision:** Tool-turn cap for the Adjudicator and Critic loops is `MAX_TOOL_TURNS = 3` (in `src/nodes/_llm_turn.py`).
**Alternatives considered:** 2 (too tight — a chain like `get_vendor_record` → `get_vendor_invoice_history` fits in 2 rounds only if a single response contains both, which the model rarely does); 5 (invites drift and cost).
**Why:** Three turns gives room for a two-step investigation plus a final answer, which matches the specific chain the Adjudicator prompt spells out for `vendor_claims`. A model that exhausts the cap without concluding produces `ESCALATE` with the fact recorded in the rationale — never a crash, never a default approval. Locked by `test_tool_loop_cap_produces_escalate_never_crash`.
2026-07-30

---

**Decision:** Critic non-convergence — when the Adjudicator holds its position through both critic rounds — is a valid terminal state. The graph terminates on `MAX_CRITIC_ROUNDS = 2` regardless of whether revision occurred. `revision_occurred: bool` is captured on state so the audit trail records whether the Adjudicator was moved.
**Alternatives considered:** Force a third round if positions diverge; treat non-convergence as failure and escalate automatically.
**Why:** Per CLAUDE.md §2.3 and the phase brief: "holding a position against a challenge is a legitimate result, not a failure to converge." The Adjudicator is authoritative for the outcome; the Critic's job is to make the strongest opposing case, not to win. `revision_occurred=False` alongside a two-round loop tells the reviewer the Adjudicator saw the challenges and rejected them — that's evidence, not a bug.
2026-07-30

---

**Decision:** Corpus adjudication distribution differs from the 5/7/5 target: observed 4 APPROVE / 10 ESCALATE / 2 REJECT.

Per-invoice outcomes vs expectation:
  - **APPROVE (4):** INV-1001, INV-1006, INV-1011, INV-1015 — all correctly clean; INV-1011 approves cleanly with no critic (the DP-001 INFO downgrade from the Phase 4 correction is doing its job).
  - **REJECT (2):** INV-1003 (Fraudster + inactive item + urgency + wire), INV-1008 (unknown vendor + two unknown items + email domain we can't verify).
  - **ESCALATE (10):** everything else, including several the target expects to REJECT (INV-1005, INV-1009, INV-1013).

The pattern: the Adjudicator is biased toward ESCALATE over REJECT, which is exactly what the "escalate liberally" instruction commanded. Per CLAUDE.md §2.3, this trade-off is deliberate — wrongly rejecting a legitimate invoice costs a vendor relationship; escalating costs a clerk four minutes. INV-1005 has multiple HIGH findings and a fabricated-legitimacy address, but no CRITICAL — the Adjudicator preferred to route it to a human. INV-1009 has three CRITICAL findings and the model still chose ESCALATE ("blank vendor and negative total need a human to determine if this is a credit memo or malformed input"). Both readings are defensible; the target's 5/7/5 was a hint, not a spec. Reported here rather than tuned toward.

**Alternatives considered:** Push the prompt to lean toward REJECT more aggressively; add "reject if you see CRITICAL" as a hard rule; tune examples to fit the distribution.
**Why:** Prompt-fitting to hit a target distribution turns the Adjudicator into a validator with LLM overhead. The whole point of Phase 5c is the model making a judgment; the numeric distribution is the outcome, not the input. If the target changes to prioritise fewer escalations later, the prompt-level lever is the "escalate liberally" paragraph — one place, easy to change, visibly a policy call.
2026-07-30

---

**Decision:** Tool use during adjudication is intermittent. Observed on this corpus: INV-1014 called 6 tools during one run, INV-1010 called 8 during another, INV-1004 called 5 including the full get_prior_invoice → get_vendor_record → get_vendor_invoice_history → get_policy chain. Other runs of the same invoices with the same prompt returned decisions without tool calls at all. Grok-4.5's reasoning process sometimes chooses to satisfy the response_format schema directly rather than call tools it deems unnecessary.
**Alternatives considered:** Set `tool_choice="required"` on first turn (forces at least one call, but manufactures tool use for cases that don't need it); split into two calls (one for tools without response_format, then a final structured call — doubles the cost).
**Why:** The plumbing works — the audit trail proves it when tools do fire, and INV-1004's DP-002 rationale correctly cites `get_prior_invoice`'s "no prior submission" result when the model does call. Grok's occasional decision to skip tool use when it thinks the findings alone are sufficient is not incorrect — it's parsimonious. Locked test: `test_tool_loop_cap_produces_escalate_never_crash` proves absence-of-tools is not a failure mode. Accepted as a Grok characteristic; documented so Phase 8's cost tracking includes tool-call latency when they do fire.
2026-07-30

---

**Decision:** Observed cost per adjudicated invoice: ~$0.035 (Grok 4.5, one full corpus pass). Total corpus cost: $0.55 for 16 unique invoices, 30–60 total LLM calls depending on critic-loop firing and tool use.
**Alternatives considered:** Extrapolating from a single-invoice test; using cached_prompt_tokens cost separately.
**Why:** This is the number Phase 8's business impact rollup uses. At $0.035/invoice, Acme's 30% error rate on ~10K invoices/year at $2M/year translates to a system cost of ~$350/year vs the multi-million-dollar exception cost the current process incurs. The ratio holds even if per-invoice cost doubles under load. Confirmed against the console-billed constant PRICE_PER_1M_OUTPUT ($6.00) which is the dominant term — reasoning tokens are the bulk of every adjudicate call.
2026-07-30

---

**Decision:** No guardrail override fired during the corpus adjudication because no Adjudicator run returned `APPROVE` on a CRITICAL-carrying invoice. The synthetic test `test_guardrail_overrides_approve_when_critical_finding_present` proves the code path works: given a mocked Adjudicator that returns APPROVE and a state carrying VN-005 CRITICAL, `adjudicate()` overrides to ESCALATE and records the override.
**Alternatives considered:** Only test the guardrail if it fires in the corpus; skip it entirely since the Adjudicator's prompt already warns against APPROVE on CRITICAL.
**Why:** Per CLAUDE.md §2.2, the override is the LAST line of defense — precisely for cases the prompt fails to catch. That code path must be locked in tests even if it never fires in practice. The mocked test is the honest way to verify it.
2026-07-30

---

**Decision:** Two related fixes for redundant tool use across nodes: a run-scoped **tool-result cache** on `GraphState` and a **prior-investigation summary** injected into every downstream agent prompt.

**Root cause diagnosed on INV-1012:** the initial adjudicator, both critic rounds, and both revised-adjudicator passes each ran their own `agent_loop`, and each `agent_loop` started with an empty conversation. The critic re-investigated what the adjudicator had found; the revised adjudicator re-investigated again; and so on. Of 41 total tool calls on INV-1012, only 12 were unique — 29 were the same lookups redone by nodes that could not see each other's context. `get_vendor_record("FastShip Ltd.")` fired 5 times, always returning the same inactive-master row.

**Fix 1 (correctness):** `GraphState.tool_result_cache: dict[str, Any]` with a dict-union reducer, keyed by `${tool_name}::${sorted-args-json}`. `run_agent_loop` accepts the current cache, serves hits at `latency_ms=0.0` (the audit marker for cache hits), stores misses, and returns the updated cache for the reducer to merge. Beyond cost, this eliminates a class of incoherence: an uncached tool could theoretically return different answers to the same question within one decision (e.g. someone updating the audit store mid-run), which is nonsense inside a single invoice's judgment.

**Fix 2 (reasoning):** `format_tool_history(tool_calls)` produces a deduplicated bullet list of every prior `(tool, args) → result` in `state.tool_calls`. The adjudicator and critic prompt templates now include a `### Prior investigation (do NOT re-run these tool calls)` section built from that history. The instruction is explicit: reuse the facts already learned; call tools only for NEW questions.

**Verified on the 3-invoice validation set** (captured during a live run before the working-tree constraint kicked in):

| Invoice | Tool calls before | Tool calls after | Model calls before | Model calls after | Cost before | Cost after |
|---|---|---|---|---|---|---|
| INV-1001 | 0 | 0 | 2 | 2 | $0.012 | $0.012 |
| INV-1004 | 21 | 6 | 17 | 14 | $0.143 | $0.140 |
| INV-1012 | 41 | 12 | 17 | 13 | $0.184 | $0.166 |

INV-1012's rationale post-fix still names the FastShip dormant-relationship chain ("Tool lookup confirms FastShip Ltd. is a real but INACTIVE master entry ... domain fastship.com on file, yet zero prior invoices and $0 history") — substance preserved. Notably, **cache hits were 0 on all three cases** because the prior-investigation prompt guidance is sufficient to prevent repeat requests in the first place; the cache is defensive backup that didn't fire on these three. Both fixes stay because either alone is insufficient — the cache guarantees coherence even if the prompt fails, and the prompt guidance saves the LLM tokens that would otherwise be spent reasoning about redundant calls.

**Test coverage:** 15 tests in `tests/test_agents.py` (mocked-provider unit tests, zero network) including 7 new locks — cache key stability, dict order invariance, history deduplication, cache-hit latency marker, cache seeding round-trip. `tests/test_graph.py` now uses `graph_llm_fake` from `tests/conftest.py` so structural graph tests do not require live LLM calls under the updated prompts. Total suite: 194 pass in ~1.3s, no network calls.
2026-07-30

---

**Decision:** Per-invoice circuit breaker on the agent loop. `MAX_TOOL_CALLS_PER_INVOICE = 12` and `MAX_MODEL_CALLS_PER_INVOICE = 8`. Enforced in `src/llm/agent_loop.py::run_agent_loop` (before each API call and before each tool execution) and in `src/nodes/scribe.py` (before the single LLM call scribe makes). On breach, raise `CircuitBreakerTripped` — a typed exception naming which cap tripped and inside which node. The graph run aborts immediately; no partial decision is returned; downstream nodes do not run.

**This is a SAFETY RAIL against runaway spend, not an intended operating limit.** A reasoning model that recurses through tool calls or an infinite critic loop could otherwise burn tokens unbounded. If a legitimate run trips one of these caps, the caps are what needs raising, not the breaker removed. Removing the breaker to make a run complete is the wrong instinct — the right move is to understand why it trips and either fix the loop or lift the cap deliberately.

**Live verification on INV-1012 (2026-07-30):** the run tripped the model-call cap in `critic_round_2` after 8 model calls had been made, wall clock 116.7s. No decision was produced because the run aborted mid-loop. The 8 completed calls, at 3 calls per agent node (2 investigation + 1 synthesis), account for the initial adjudicate + critic round 1 + revised adjudicate; critic round 2's first call would have been the 9th and was blocked. Cassettes for the 8 completed calls are recorded on disk — they are partial-run recordings and future replays that need INV-1012 will need cassettes for the missing steps or a raised cap. Ordinary tools tests (`tests/test_agents.py`) monkeypatch the module-level cap constants to 999 via `graph_llm_fake` in `tests/conftest.py` because the mocked provider generates 2 model calls per agent node and would otherwise trip on multi-round critic scenarios that the tests are validating. The nodes read the cap through the module attribute at call time (`agent_loop.MAX_MODEL_CALLS_PER_INVOICE`) so monkeypatch flows through; `run_agent_loop`'s caps default to `None` and resolve via `if X is None: X = MAX_...` for the same reason.

**Two new tests in `tests/test_agents.py`** lock the breaker behaviour under mocked providers: `test_circuit_breaker_trips_on_model_cap` and `test_circuit_breaker_trips_on_tool_cap`.
2026-07-30

---

**Decision:** Critic-loop convergence check. `route_after_adjudicate` now skips subsequent critic rounds when the adjudicator did NOT revise its outcome in the immediately-preceding round. `MAX_CRITIC_ROUNDS = 2` was being applied unconditionally, so every non-trivial invoice ran the full five-node ladder regardless of whether the first challenge changed anything. A critic exists to test a conclusion; a conclusion that survived one challenge unchanged is unlikely to be moved by a second challenge of the same shape.

**This is a convergence check, not a cost optimisation** — though it is also that. Running the loop again when the first pass produced no movement is not just wasted spend, it is a signal the loop is not doing its job. The check reads `state.revision_occurred` (cumulative across prior adjudicate calls); if `rounds_so_far >= 1 and not revision_occurred`, route directly to `scribe` or `route_outcome`.

**Scribe deliberately does NOT enforce the per-invoice model-call breaker.** Scribe is a single non-recursive LLM call producing a human-facing note. The breaker exists to catch agent-loop runaway (tool recursion, endless critic loops). Enforcing it in scribe just to be consistent would sometimes lose the note on an otherwise completed decision — bad trade for a bounded call. Test locked at `test_scribe_produces_note_on_escalate`.

**Live verification on INV-1012 (2026-07-30, second run):**

| metric | previous (no convergence check) | this run |
|---|---|---|
| total tool calls | 41 | **11** |
| unique tool calls | 12 | 11 (all unique, 0 cache hits) |
| total model calls | 17 | **9** |
| cost | $0.184 | **$0.102** |
| wall clock | 193s | 132.6s |
| circuit breaker tripped | n/a | no |
| outcome | ESCALATE | ESCALATE |
| critic rounds | 2 | 1 |
| revision occurred | false | false |

The FastShip dormant-relationship chain is present in the final rationale ("QuickShip Distributers is not in the vendor master (VN-001) and claims to be formerly FastShip Ltd., which is an inactive master vendor with zero prior invoice history (VN-002, VN-004). Tool checks confirm FastShip Ltd. is inactive and has never been paid..."). Substance preserved end-to-end.

**Test:** `tests/test_graph.py::test_critic_stops_after_round_1_if_no_revision` locks the convergence semantics using the fake provider. The old assertion "critic fires 2 rounds" was replaced with "critic fires 1 round" because the fake provider always returns the same ESCALATE — no revision — and the new gate correctly stops after round 1.
2026-07-30

---

**Decision:** `MAX_CRITIC_ROUNDS` dropped from 2 to 1. A single critic round runs on every invoice meeting `CRITIC_TRIGGER`; there is no second round regardless of whether revision occurred.
**Alternatives considered:** Keep MAX=2 and raise the model-call cap; keep MAX=2 with the convergence gate; introduce a differently-shaped round 2.
**Why:** The second critic round receives the same invoice, same findings, same tools, and same prompt as the first — its only new input is the revised rationale. It is a re-roll of the same challenge, not a new one. The INV-1003 diagnostic showed the convergence gate treating "revision occurred" as license for another round, meaning every case complex enough to warrant revision automatically tripped the model-call budget. A critic that runs until the budget stops it is not reasoning, it is ceremony.

**Future work (not implemented, out of scope):** a second round would be justified if given a DIFFERENT job — e.g. "assess whether the adjudicator's revision is sound" rather than re-issuing the original challenge. That is a design change, not a parameter change. The convergence check was removed with this policy since it became dead code once MAX=1.
2026-07-30

---

**Decision:** `MAX_MODEL_CALLS_PER_INVOICE` raised from 8 to 10. Scribe remains excluded from the count.
**Alternatives considered:** Keep at 8; lower `MAX_INVESTIGATION_TURNS` from 3 to 2; make revised adjudicate single-turn.
**Why:** Cap 8 was set assuming revised adjudicate uses 2 calls (1 investigation + 1 synthesis). INV-1004 tripped when its revised adjudicator legitimately used 3 (2 investigation turns + synthesis) on the corpus's hardest duplicate case. True worst-case under the single-critic policy is 3 (adjudicate) + 3 (critic) + 3 (revised adjudicate) = 9 agent-loop calls; cap 9 sits exactly at the ceiling and trips on any variance. Cap 10 gives one slot of headroom. The cap remains a runaway-spend rail, not a design constraint — it must never trip on a legitimate deep investigation, and INV-1004's revised adjudicator taking a second look at a DP-002 collision is legitimate.

Reducing `MAX_INVESTIGATION_TURNS` to 2 was rejected: that would cap the investigation itself rather than the budget. INV-1004's initial adjudicator used all 3 turns to unpack the duplicate; forcing 2 would truncate reasoning on the hardest case for no gain.
2026-07-30

---

**Decision:** Full corpus completed successfully live on 2026-07-30 under the new caps and policies. 16/16 invoices ran through the graph, zero circuit-breaker trips, all outcomes and rationales produced. Distribution and per-invoice metrics:

| # | file | invoice | outcome | crit | rev | mdls | tls | $cost | sec |
|---|---|---|---|---|---|---|---|---|---|
| 1 | invoice_1001.txt | INV-1001 | APPROVE | 0 | | 2 | 0 | $0.01199 | 14.9 |
| 2 | invoice_1002.txt | INV-1002 | ESCALATE | 1 | | 9 | 8 | $0.09178 | 130.2 |
| 3 | invoice_1003.txt | INV-1003 | ESCALATE | 1 | yes | 9 | 10 | $0.09673 | 128.8 |
| 4 | invoice_1004.json | INV-1004 | REJECT | 1 | yes | 10 | 6 | $0.08626 | 107.9 |
| 5 | invoice_1005.json | INV-1005 | ESCALATE | 1 | yes | 9 | 10 | $0.08720 | 90.1 |
| 6 | invoice_1006.csv | INV-1006 | APPROVE | 0 | | 2 | 0 | $0.01214 | 8.1 |
| 7 | invoice_1007.csv | INV-1007 | ESCALATE | 1 | | 9 | 9 | $0.09307 | 116.8 |
| 8 | invoice_1008.txt | INV-1008 | REJECT | 1 | yes | 9 | 10 | $0.08737 | 92.6 |
| 9 | invoice_1009.json | INV-1009 | REJECT | 1 | | 8 | 7 | $0.07280 | 84.2 |
| 10 | invoice_1010.txt | INV-1010 | ESCALATE | 1 | | 9 | 8 | $0.08229 | 90.3 |
| 11 | invoice_1011.pdf | INV-1011 | APPROVE | 0 | | 3 | 1 | $0.02012 | 17.2 |
| 12 | invoice_1012.pdf | INV-1012 | ESCALATE | 1 | | 9 | 11 | $0.10049 | 114.9 |
| 13 | invoice_1013.json | INV-1013 | ESCALATE | 1 | | 9 | 9 | $0.10963 | 132.6 |
| 14 | invoice_1014.xml | INV-1014 | ESCALATE | 1 | | 9 | 7 | $0.07684 | 91.1 |
| 15 | invoice_1015.csv | INV-1015 | APPROVE | 0 | | 2 | 0 | $0.01178 | 7.5 |
| 16 | invoice_1016.json | INV-1016 | ESCALATE | 1 | | 9 | 9 | $0.08171 | 81.2 |

Distribution: **4 APPROVE / 9 ESCALATE / 3 REJECT** (vs 5/7/5 target). Divergence from target is toward ESCALATE, consistent with the "escalate liberally" prompt guidance. INV-1011 escalated where it approved on a prior run — Grok non-determinism on a clean invoice; not a regression of the pipeline. Total spend $1.12223, mean per-invoice $0.07014. Extraction cassettes (Phase 3) unchanged; 40+ new adjudicator/critic/scribe cassettes recorded.

Also added a running cost printout to `scripts/adjudicate_corpus.py` after each completed invoice — `cum=$X.XXXXX` so an interrupted batch immediately shows where spend stands rather than requiring reconstruction from cassettes.
2026-07-30

---

**Decision (known issue, not fixed here):** INV-1004 resolved to REJECT in the 2026-07-30 corpus run, differing from earlier runs' ESCALATE. Root cause is an infrastructure gap, not a change in judgment. `get_prior_invoice(INV-1004)` returned `found=False` because the audit store is empty — Phase 6 (audit persistence, human gate settlement) has not yet been built, so the tool has no records to serve. There is nothing on disk saying "the other INV-1004 file exists or was already touched."

The Adjudicator's rationale then treated that infrastructure absence as a business fact: "no prior submission exists, so DP-002 is a live double-pay risk requiring hard rejection." That reasoning would be correct in a production system with a populated audit store; here it is not, because the sibling file `invoice_1004_revised.json` is a real second submission that already exists on disk and that the DP-002 finding (from `find_duplicates()` in the batch pre-pass) explicitly flags.

The DP-002 finding says the two files exist. `get_prior_invoice` says no prior payment record exists. Both are true; the Adjudicator conflated them and concluded REJECT. Expected outcome once the audit store carries real prior-invoice context — either seeded from the batch pre-pass or filled by Phase 6's settlement records — is ESCALATE, matching the "human confirms which of the two INV-1004 files is authoritative" resolution path that all prior runs produced.

**Fix location** (out of scope for this build): Phase 6 wires the audit store; either `get_prior_invoice` learns to read the current batch's sibling files, or `find_duplicates`' emitted DP-002 evidence is elevated in the Adjudicator prompt so the tool's `found=False` cannot dominate. Both are Phase 6 work.

**Revisit after Phase 6.** Do not tune the prompt against this now — the tool is lying by omission and the prompt is doing the best it can with the answer it received.
2026-07-30

---

**Decision:** In `_invoice_summary` (adjudicator/critic/scribe context builder), emit `source_file` as basename only (`Path(source_file).name`) instead of the full stored path. The full absolute path stays on the Invoice object for the audit store and dashboard; only the LLM-facing view is stripped.
**Alternatives considered:** (a) resolve to repo-relative and pass through — still bakes the checkout layout into cassettes; (b) rewrite existing cassettes to strip the prefix — would forge responses the model never gave to the modified request; (c) leave paths absolute and require re-recording per machine — defeats the "cold clone, no API key" objective.
**Why:** The recorded cassettes contained absolute paths (`/Users/maximiliandaneker/…/data/invoices/…`) inside the LLM message content. Any cold clone in a different filesystem location produced a different SHA-256 fingerprint and missed every agent-node cassette. Basename is the smallest identifier the Adjudicator needs; it makes cassettes machine-independent by construction. Invalidated 274 of 449 recordings; re-recorded via a fresh live corpus run.
2026-07-31

---

**Decision:** `make demo` clears `runs/checkpoints.sqlite` at start (added `@rm -f runs/checkpoints.sqlite` alongside the existing `audit-reset` dependency). Interactive and queue modes still checkpoint normally.
**Alternatives considered:** (a) point demo mode at a throwaway checkpoint path via a demo-only env var — extra plumbing for a one-line fix; (b) rely on reviewers to clear manually — invisible failure mode; (c) disable the checkpointer entirely for `--replay` — would break the human-gate resume flow if a real run ever switched into replay mid-stream.
**Why:** The checkpointer's job is to make interrupted escalations resumable in real operation — correct behavior there. Demo mode has the opposite requirement: a reviewer must see identical output on run 1, run 2, and run 5. Without the clear, a second `make demo` resumed each thread from its prior terminal state and produced truncated / mismatched output. Discovered while smoke-testing the cassette-portability fix: the first replay after a live record hit a cache miss because the checkpointer resumed mid-graph on state that hadn't existed when the cassette was recorded.
2026-07-31

---

**Decision (Phase 7 severity calibration):** No severity values in `src/validators/` are changed in this phase; every proposed calibration is recorded as *deferred* below. The `docs/exception-taxonomy.md` file is updated with observed corpus behavior and a distribution reconciliation, but no code was touched.
**Alternatives considered:** (a) adjust severities where the calibration argument is strong — TM-002 → INFO, PR-004 → INFO — and re-record only the affected invoices; (b) full severity re-tune and full corpus re-record. Both cost ~$1.10 in live API spend for the re-record.
**Why:** The finding's severity string is emitted into the LLM message content (see `_build_context` in `src/nodes/adjudicate.py`), so severity is part of the cassette request fingerprint. Any severity change invalidates recorded cassettes regardless of whether it would flip an outcome. Under a zero-live-calls constraint this session, the only safe severity edit is one on a code that never fired — which is uninteresting. Deferring is the honest move; the recorded corpus IS the calibration evidence for what shipped.

**Deferred severity adjustments** (record only, do not apply):
- **TM-002 LOW → INFO** — fires only on INV-1016 where IN-001 HIGH was the actual driver. Never contributed to a routing decision. Defer.
- **PR-004 LOW → INFO** — fires on INV-1003, INV-1008, INV-1016; every case had a HIGH finding present. Defer.
- **FR-001 LOW confirmed** — fired only on INV-1003 alongside VN-001+PO-001+IN-002. Never drove alone. LOW is correct.
- **FR-002 MEDIUM confirmed** — same case. MEDIUM appropriate.
- **FR-003 MEDIUM confirmed** — INV-1005 co-occurred with VN-001+IN-003+PO-001. MEDIUM appropriate.
- **PO-002 MEDIUM confirmed** — participated in escalation on INV-1008 and INV-1012 as a coincidence signal alongside stronger findings, never drove alone.

**Deferred structural observations (not severity, not applied):**
- **INV-1001 zero-findings adjudicator variance** — the recorded corpus has INV-1001 as APPROVE, but an earlier live run escalated it. A zero-findings invoice under threshold should never reach the model; a deterministic fast-path (skip adjudicate/critic/scribe entirely) would eliminate the coin-flip and cut cost/latency for the 25% of the corpus that has no findings. Defer to Phase 9 or 10 — implementing now would require re-recording (the pruned nodes would remove cassettes from the flow) and would tune against zero real signal from the current corpus.
- **INV-1009 three CRITICALs → ESCALATE** — the model routed correctly per §2.2 (no auto-APPROVE), but a rules-based fast-path "≥2 CRITICALs of different codes → auto-REJECT" would remove one clerk touch without loss of judgment. Defer for the same reason as above.
- **PR-002 unused** — the validator exists but no corpus invoice trips it. Taxonomy annotated. Consider adding an adversarial invoice in Phase 10 rather than removing the validator.
2026-07-31

---

**Decision (Phase 7 reporting):** `make report` reads the audit store only — no API calls, no writes. Outcome distribution, straight-through rate, queue depth, exceptions by category, cost by node type (four-category token breakdown), and total cost.
**Alternatives considered:** (a) HTML/JSON report to disk — deferred to Phase 11 dashboard, which is the intended shape; (b) include per-invoice latency — `runs.started_at` is not currently populated (only `finished_at` is), and populating it would need a graph change with cassette implications. Skipped rather than emit misleading data.
**Why:** The audit schema already has the queryable data Phase 8 promised; pulling `make report` forward keeps demo-time visibility honest without any code that could change recorded outcomes.
2026-07-31

---

**Decision (Phase 7 taxonomy edit constraint):** Any `INV-\d{4}` reference added to or removed from the **Corpus** column of `docs/exception-taxonomy.md` invalidates recorded cassettes and forces a live re-record. The `get_policy` tool regex-extracts invoice IDs from that column and returns them as `corpus_examples`, which then flows into the LLM message content on any downstream `get_policy(<code>)` tool call. Reserve INV-XXXX literals in the taxonomy for the reconciliation section (which is prose, not table rows).
**Alternatives considered:** (a) drop `corpus_examples` from the tool return — semantic change to the tool contract, defer; (b) parse from a separate manifest file — extra plumbing.
**Why:** Discovered during Phase 7 while annotating TM-002 with its observed corpus case (INV-1016). Adding "INV-1016" to the Corpus cell produced a fresh cassette miss on the second replay, because `get_policy(TM-002)` now returned `corpus_examples: ["INV-1016"]` where recorded cassettes had `[]`. Reverted the corpus cell to non-INV-referencing text; observation kept in the reconciliation section where the tool's regex doesn't see it.
2026-07-31

---

**Decision (Phase 8 observability):** JSONL log path is written silently — not printed to stdout. Manifest timestamp appears only in the JSONL file, not in the CLI header.
**Alternatives considered:** (a) print the path so reviewers can see it — timestamp in the path breaks the byte-identical determinism check the demo relies on; (b) print the path with `--verbose` — extra flag noise for a file trivially found via `ls runs/*.jsonl`.
**Why:** Two invariants collide. The demo must be byte-identical run-to-run so a reviewer can confirm nothing broke since Phase 7. The JSONL file must be timestamped so a reviewer can distinguish runs on disk. Splitting them — timestamped filename, silent about it in stdout — preserves both. The manifest itself keeps the timestamp inside the file for provenance.
2026-07-31

---

**Decision (Phase 8 deferred):** `runs.started_at` remains unpopulated in the audit store. The `finished_at` column plus the JSONL `elapsed_seconds` field cover the per-invoice wall-clock need.
**Alternatives considered:** Have `route_outcome` or the graph entry stamp `started_at` from state. The stamping would need a new GraphState field seeded at `main.py`, threaded through, and written by the route node — a change with cassette risk if any node reads the timestamped state into an LLM message.
**Why:** Populating it now buys almost nothing for the current corpus (all invoices replay in < 1 s) and creates the very risk this phase is meant to avoid. Real wall-clock matters only for live runs and lands cheaply in Phase 9 alongside the eval harness, which will re-record anyway.
2026-07-31

---

**Decision (Phase 9 ground-truth correction):** Added `AR-004` to INV-1009's `must_fire` list. Original draft only listed `AR-002` (subtotal mismatch). AR-004 (stated_total ≠ subtotal + tax + additional_charges) also legitimately fires: subtotal=1000 stated, tax=0, additional_charges=[], expected total=1000, actual stated=-250, delta -1250.
**Alternatives considered:** Leave the finding unlisted and treat every fire of AR-004 on INV-1009 as an "unexpected" report (the eval doesn't fail on unexpected findings, only on missing must-fires).
**Why:** The finding is a legitimate deterministic result, not a system artifact. Suppressing it from ground truth would misrepresent what the validator suite is supposed to do on a signed-total-mismatch invoice. Recording as an oversight fix, per the Phase 9 rule that ground-truth corrections are legitimate but scoring-flattering adjustments are not.
2026-07-31

---

**Decision (Phase 9 named defect):** The batch loop in `main.py` processes duplicate-pair invoices in alphabetical filename order, so INV-1011.pdf is handled before INV-1011.txt and the txt (more-complete source) is skipped. The `find_duplicates` validator emits DP-001 with a "retained" hint (based on completeness score), but the batch iterator does not consult it. Result: INV-1011's `payment_terms` field is lost (present in txt, absent in pdf).
**Alternatives considered:** (a) Fix now — change the batch iterator to honor the retained-file recommendation. Cassette-affecting: retained-file swap would change source_file → different graph state → different cassette keys → live re-record cost of at least INV-1011 (~$0.02) plus the risk of ripple effects on INV-1012/1013 which are also duplicate pairs.
(b) Fix in Phase 10 alongside the adversarial invoice work (which may re-record anyway).
**Why:** The defect is real and named in `docs/eval-results.md`. The 1-of-131 extraction-field impact is scoped and honest to disclose. Fixing it inside Phase 9's zero-live-calls constraint is impossible; deferring with the defect named in the ships-with document is the correct trade.
2026-07-31

---

**Decision (Phase 9 eval CORPUS is relative path):** `eval/run_eval.py` uses `CORPUS = Path("data/invoices")` (relative) not `REPO_ROOT / "data" / "invoices"` (absolute). The relative form matches what `main.py --batch` produces.
**Alternatives considered:** Absolute path everywhere — more robust to being invoked from another cwd.
**Why:** The audit store and `find_duplicates` key on `source_file` (which is `str(path)`); if the eval passes absolute paths and `make demo` passes relative paths, they produce different audit-store rows and different duplicate-group keys. That would break cassette replay on INV-1004 (duplicate pair) even though Phase 6 normalized the LLM-facing view. Discovered while smoke-testing `make eval` from a fresh state; the miss was at INV-1004's `adjudicator_revised` call. Constraint: any consumer of source_file that isn't the LLM prompt is coupled to the concrete path form used at record time.
2026-07-31

---

**Decision (duplicate-selection fix — semantic-hash scoped):** `select_batch_retentions` in `src/validators/duplicates.py` picks which file of each `by_invoice_number` group the batch orchestrator should process. Scoped by semantic_hash: matching-hash groups (DP-001) go through `pick_retained` (completeness + basename tie-break); differing-hash groups (DP-002) keep alphabetical-first, no auto-selection. `main.py`, `src/batch.py`, and `eval/run_eval.py` all consume it.
**Alternatives considered:** (a) apply `pick_retained` unconditionally over every group — was the first attempt; would have silently swapped INV-1004's original for its revised submission because the revision has more line items. Caught before commit by the `make demo` md5 stop-rule in the task prompt. (b) revert and keep the INV-1011 miss as a documented known defect — rejected in favor of a real fix once the scoping approach was clear. (c) parse `find_duplicates`' DP-001/DP-002 finding output — more indirect than reading semantic_hash directly.
**Why:** The completeness rule is only meaningful for a DP-001 group ("same invoice, two files, pick the more-complete extraction"). Applied to DP-002 ("same number, DIFFERENT submissions"), it makes a decision that belongs to the human gate. The semantic-hash check reads the same signal `find_duplicates` uses to distinguish the two cases, and locates the guard at the batch boundary where it belongs. INV-1011 re-recorded live at $0.01944; no other cassette touched. Extraction accuracy moved 130/131 → 131/131 (100%). `make demo` byte-identical at md5 d31895b6b7320e729324b4e56d93a4f8. Near-miss narrated in `docs/eval-results.md` because catching a wrong fix before commit is part of the story worth telling.
2026-07-31

---

**Decision (Phase 10 adversarial set design):** Four invoices in `data/adversarial/` targeting AR-001, AR-003, PR-002, and PO-002-as-pair. Kept in a separate directory from the provided corpus and a separate `eval/ground_truth_adversarial.yaml` so provided-corpus and authored numbers are never conflated in reporting. `make demo` remains the 16-invoice provided corpus; `make demo-adversarial` and `make eval-adversarial` are the authored-set entrypoints. main.py gained a `--corpus DIR` argument to make the batch reusable across both sets.
**Alternatives considered:** (a) mix the adversarial invoices into `data/invoices/` — would inflate the provided-corpus numbers and let the authored cases hide their misses; (b) generate adversarial PDFs via a `generate_test_pdfs.py` fork per the Phase 4 plan — stayed cut, adds rendering variance without exercising anything the txt/csv/json adapters can't already reach for the target codes; (c) score adversarial and provided together — same conflation problem as (a).
**Why:** Authored corpus should never boost or dilute the numbers the reviewer reads about the provided corpus. Ground truth was written BEFORE running the invoices — git log confirms `ground_truth_adversarial.yaml` was committed with the source files, before any live run. Live re-record cost the adversarial batch: **$0.09781** (within the $0.50 rail).
2026-07-31

---

**Decision (Phase 10 discovery — AR-003 is dead code):** The adversarial exercise proved that AR-003 (stated tax ≠ subtotal × stated rate) is documented in `docs/exception-taxonomy.md` but has no implementation in `src/validators/arithmetic.py`. The arithmetic module's docstring already noted "we do not have tax_rate on the schema; we approximate by checking tax as a component of the grand-total sum, not as an independent check" — the adversarial invoice INV-2002 (subtotal 3500, tax_rate 0.08, tax_amount 210 not 280) makes this observable rather than merely documented. Ground truth for INV-2002 lists AR-003 as `may_fire`, not `must_fire`, so the eval accurately reports the current implementation.
**Alternatives considered:** (a) implement AR-003 in this phase — requires a `tax_rate` field on the Invoice schema, all adapters updated to parse it, then a validator addition. Schema change would ripple into `_invoice_summary`, invalidating cassettes. Deferred. (b) delete the AR-003 row from the taxonomy — would invalidate cassettes via the `get_policy` extraction-surface hash (`test_taxonomy_frozen`). Deferred.
**Why:** The adversarial exercise's point is discovery. Removing a "gap the corpus never exercised" annotation only to replace it with "dead code" is more informative than shipping the annotation. Recorded here; the fix is future work in a phase that can afford to re-record.
2026-07-31

---

**Decision (Phase 10 threshold-structuring result):** ADV-2001 and ADV-2004 are a designed pair — same vendor (Widgets Inc.), 3 days apart, each $9,712.50 (in the 5% near-threshold band), jointly $19,425. Each invoice individually trips PO-002; the joint pattern goes unflagged. This is the documented honest result — the cross-invoice aggregator was cut in Phase 7 planning and remains cut. The adversarial exercise documents the gap by observation rather than by speculation.
**Alternatives considered:** Build a cross-invoice detector for this build — significant new code, changes graph structure, cassette re-record cost across the corpus.
**Why:** The Phase 10 spec ("state the honest result") applies. A build that reports "pair pattern individually flagged but not cross-aggregated, here is the concrete case that proves it" beats a build that either omits the case or fabricates aggregation.
2026-07-31

---

**Decision (Phase 10 clarification — ground-truth trail for INV-2002):** The single commit `8df41da` created `eval/ground_truth_adversarial.yaml` with `must_fire: []` and `may_fire: [AR-003]` for INV-2002 from the outset. That was authored FIRST, and the live run was confirmation.

Sequence: while designing ADV-2002 I grepped `src/validators/arithmetic.py` for `AR-003` (empty result) and then for `AR-\|tax_rate\|_tax\b` (AR-003 absent from the AR- list). The module docstring explicitly noted "we do not have tax_rate on the schema; we approximate by checking tax as a component of the grand-total sum, not as an independent check." So the ground truth was written knowing the check was unimplemented — `may_fire` rather than `must_fire` was the honest classification, and the yaml comment stated the reason plainly.

Case (a) — not (b): no post-run correction. Prior wording in `docs/eval-results.md` and the earlier DECISIONS entry called the outcome a "discovery" and said the exercise "surfaced" the gap — that oversells it. The exercise CONFIRMED, by an observable authored case, an implementation gap I had already read out of the validator source. The value is that the confirmation is now in a committed artifact anyone can rerun, not that the gap was previously unknown to the author.
2026-07-31

---

**Decision (Phase 11 dashboard architecture):** FastAPI + Jinja2 + Tailwind CDN + Alpine.js CDN per CLAUDE.md §3b. Zero build step. Read-only over `runs/audit.sqlite` for all aggregate metrics, plus a re-extraction pass via `router_extract(source_file)` for detail views (line items, corrections, extraction_confidence — fields the audit store doesn't persist). Re-extraction runs in `LLM_MODE=replay`; deterministic adapters return instantly and LLM adapters hit committed extractor cassettes. Zero API calls.
**Alternatives considered:** (a) persist the full Invoice object to the audit store so the dashboard doesn't need to re-extract — schema change, cassette-invalidating, deferred; (b) hydrate a read model into a separate SQLite file — extra plumbing for one field type; (c) render extraction detail from cassette contents — cassettes are LLM I/O records, not extracted objects.
**Why:** The dashboard reads. Re-extraction against cached adapter results is instant and honest — the same code path the pipeline uses. No dual source of truth.
2026-07-31

**Decision (Phase 11 provided vs authored separation):** The dashboard filters and separates the two corpora at every aggregate boundary — hero metrics, exceptions-by-category bars, queue table filter. Never blended silently. Corpus attribution is derived at query time from `source_file` prefix (`data/invoices/` = provided, `data/adversarial/` = adversarial).
**Why:** Same reason the eval keeps them separate. The dashboard is a manager surface; blending would misrepresent both the reviewer's baseline and the exercised-code numbers.
2026-07-31

**Decision (Phase 11 audit-store limitations noted):** Two fields the dashboard would like but the audit store lacks: (1) full `Invoice` object per run (line items, corrections, extraction_confidence) — solved by re-extraction; (2) per-invoice wall-clock (`runs.started_at` populated) — deferred, same as Phase 8. Both are named here rather than backfilled: extending the audit store's WRITE path would carry cassette risk if any node reads it into an LLM message, and Phase 11's scope is READ.
2026-07-31

---
