"""Policy lookup — returns the documented rationale for a finding code.

Parses the tables in `docs/exception-taxonomy.md`. The taxonomy is the
single source of truth; the tool never returns a recommended action or
an assessment of severity.

Severity is intentionally NOT returned by this tool. Findings carry their
own severity; the tool's job is to explain what the code MEANS. Returning
severity here would blur the fact/judgment line the taxonomy enforces.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from src.tools.models import PolicyQuery, PolicyResult


TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "docs" / "exception-taxonomy.md"

# Table row shape: | `CODE` | severity | trigger | detection | rationale | corpus |
_ROW_RE = re.compile(r"^\|\s*`([A-Z]{2}-\d{3})`\s*\|(.+)\|\s*$")
_CODE_MENTION_RE = re.compile(r"INV-\d{4}(?:_\w+)?")


@lru_cache(maxsize=1)
def _parse_taxonomy() -> dict[str, dict[str, str | list[str]]]:
    """Return {code: {trigger, detection, rationale, corpus_examples}}."""
    if not TAXONOMY_PATH.exists():
        return {}

    entries: dict[str, dict[str, str | list[str]]] = {}
    for line in TAXONOMY_PATH.read_text().splitlines():
        m = _ROW_RE.match(line)
        if not m:
            continue
        code = m.group(1)
        parts = [p.strip() for p in m.group(2).split("|")]
        # Expected columns after code: severity, trigger, detection, rationale, corpus
        if len(parts) < 5:
            continue
        _severity, trigger, detection, rationale, corpus = parts[:5]
        entries[code] = {
            "trigger": trigger,
            "detection": detection,
            "rationale": rationale,
            "corpus_examples": _CODE_MENTION_RE.findall(corpus),
        }
    return entries


def get_policy(query: PolicyQuery) -> PolicyResult:
    entries = _parse_taxonomy()
    row = entries.get(query.finding_code.strip())

    if row is None:
        return PolicyResult(
            code=query.finding_code,
            found=False,
            trigger=None,
            detection=None,
            rationale=None,
            corpus_examples=[],
        )

    return PolicyResult(
        code=query.finding_code,
        found=True,
        trigger=str(row["trigger"]),
        detection=str(row["detection"]),
        rationale=str(row["rationale"]),
        corpus_examples=list(row["corpus_examples"]),  # type: ignore[arg-type]
    )
