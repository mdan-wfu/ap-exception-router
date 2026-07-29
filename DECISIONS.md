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
