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
