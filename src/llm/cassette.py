"""Cassette store: record/replay so the corpus runs offline with no API key.

The cassette key is a SHA-256 over the request fingerprint:
  - model identifier
  - full serialized messages (including every system prompt character)
  - tool definitions
  - response-format schema

Prompt text is included on purpose. Editing prompts/extractor.md changes the
messages, which changes the key, which forces a cache miss. Without this rule
you spend hours debugging a change that never took effect.

Modes are enforced in provider.chat():
  live   — always call the API, record the result
  replay — never call the API; a cache miss is a hard, loud error
  auto   — replay on hit, live on miss, record the result
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.llm.provider import LLMResult


DEFAULT_CASSETTE_DIR = Path(__file__).resolve().parents[2] / "data" / "cassettes"

# xAI API keys begin with `xai-`. Any string matching this shape in a
# cassette payload is a redaction failure.
_KEY_PATTERN = re.compile(r"xai-[A-Za-z0-9_\-]{6,}")


class RedactionError(Exception):
    """A cassette payload contained credential-looking content."""


class CassetteStore:
    def __init__(self, root: Path | str = DEFAULT_CASSETTE_DIR) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- Keying -------------------------------------------------------------

    def _fingerprint(self, request: dict[str, Any]) -> dict[str, Any]:
        # Only the fields that materially affect the response go into the key.
        # max_completion_tokens is intentionally omitted: increasing the budget
        # for the same prompt should be a cache hit for the recorded content.
        return {
            "model": request.get("model"),
            "messages": request.get("messages"),
            "tools": request.get("tools"),
            "response_format": request.get("response_format"),
        }

    def compute_key(self, request: dict[str, Any]) -> str:
        blob = json.dumps(
            self._fingerprint(request), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    # -- Read / write -------------------------------------------------------

    def get(self, request: dict[str, Any]) -> dict[str, Any] | None:
        path = self._path(self.compute_key(request))
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def put(self, request: dict[str, Any], result: "LLMResult") -> None:
        key = self.compute_key(request)
        payload = {
            "fingerprint": self._fingerprint(request),
            "content": result.content,
            "tool_calls": result.tool_calls,
            # Exclude the computed `cost_usd` field: cassettes store token
            # counts only, and cost is recomputed at read time from current
            # pricing. Freezing cost into a cassette makes every recording
            # stale the moment prices change.
            "model_call": json.loads(
                result.model_call.model_dump_json(exclude={"cost_usd"})
            ),
        }
        _reject_credential_leaks(payload)
        self._path(key).write_text(json.dumps(payload, indent=2, sort_keys=True))

    # -- Safety net ---------------------------------------------------------

    def scan_for_credentials(self) -> list[str]:
        """Return relative paths of any cassette files whose contents contain
        an xAI-key-shaped string. Meant to be run as a test and in CI."""
        offenders: list[str] = []
        for p in sorted(self.root.glob("*.json")):
            if _KEY_PATTERN.search(p.read_text()):
                offenders.append(str(p))
        return offenders


def _reject_credential_leaks(payload: Any) -> None:
    """Recursively scan payload strings. Raise if anything looks like a key."""
    if isinstance(payload, str):
        if _KEY_PATTERN.search(payload):
            raise RedactionError(
                f"Cassette payload contains credential-looking content: "
                f"{payload[:64]!r}..."
            )
    elif isinstance(payload, dict):
        for v in payload.values():
            _reject_credential_leaks(v)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            _reject_credential_leaks(v)
