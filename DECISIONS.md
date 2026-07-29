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
