"""Guard the extraction surface of docs/exception-taxonomy.md against
inadvertent edits that would invalidate recorded cassettes.

Only the portion the `get_policy` tool actually reads is hashed — table rows
that parse into (trigger, detection, rationale, corpus_examples). Free-text
edits to the preamble, section prose, the reconciliation section, and code
comments do not trip this test.

If this test fails, an edit changed a tool-visible field. The recorded
adjudicator/critic cassettes were made against the old parsed content, so
they will miss on the next replay. Options:

  1. Revert the edit (usual case — the edit was accidental).
  2. Deliberately re-record the affected invoices live, then update
     EXPECTED_SURFACE_HASH below to the new value AND add a DECISIONS
     entry documenting the re-record.

See DECISIONS 2026-07-31 Phase 7 "Taxonomy edit constraint" for the
underlying doc-tool coupling.
"""
from __future__ import annotations

import hashlib
import json

from src.tools.policy_tool import _parse_taxonomy


EXPECTED_SURFACE_HASH = "f8a399f16fa4d392bddcf87c997e23e36e14ac3586ba2422900e9075204dc88f"


def _current_surface_hash() -> str:
    _parse_taxonomy.cache_clear()
    entries = _parse_taxonomy()
    # sort_keys makes the hash independent of dict-insertion order.
    blob = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def test_get_policy_extraction_surface_is_frozen():
    actual = _current_surface_hash()
    if actual == EXPECTED_SURFACE_HASH:
        return
    raise AssertionError(
        "docs/exception-taxonomy.md: get_policy extraction surface changed.\n"
        f"  expected hash: {EXPECTED_SURFACE_HASH}\n"
        f"  actual hash:   {actual}\n"
        "\n"
        "A table-row edit altered a field the get_policy tool returns\n"
        "(trigger / detection / rationale / corpus_examples). Recorded\n"
        "cassettes were made against the previous parsed content and will\n"
        "miss on the next replay of any invoice whose adjudicator or critic\n"
        "calls get_policy for an affected code.\n"
        "\n"
        "Fixes:\n"
        "  1. Revert the edit (usual case), or\n"
        "  2. Re-record affected invoices live and update\n"
        "     EXPECTED_SURFACE_HASH in this test to the new value, and\n"
        "     add a DECISIONS entry noting the deliberate change.\n"
        "\n"
        "See DECISIONS 2026-07-31 Phase 7 'Taxonomy edit constraint'."
    )
