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
