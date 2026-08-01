"""Manifest and structured JSON-lines logging for batch runs.

Two purposes:

1. Print a manifest header at run start so every recorded batch is
   attributable to the exact configuration that produced it (model,
   mode, cassette count, config constants, git SHA). This is what makes
   the Phase 9 eval numbers defensible.

2. Write a `.jsonl` file per batch (or per single-invoice run) under
   `runs/` — machine-readable twin of the CLI output. First line is a
   manifest record; subsequent lines are one per invoice.

The manifest and log emission touch neither the graph nor the LLM
request path, so they cannot affect recorded cassette fingerprints.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import config as cfg


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "runs"


def _git_sha() -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        return r.stdout.strip() or None
    except Exception:
        return None


def _git_dirty() -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


def _cassette_count() -> int:
    d = REPO_ROOT / "data" / "cassettes"
    if not d.exists():
        return 0
    return sum(1 for _ in d.glob("*.json"))


def build_manifest() -> dict[str, Any]:
    """Snapshot of configuration + provenance at the moment the batch starts."""
    return {
        "type": "manifest",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git": {"sha": _git_sha(), "dirty": _git_dirty()},
        "llm": {
            "mode": os.environ.get("LLM_MODE", cfg.LLM_MODE),
            "model": cfg.GROK_MODEL,
        },
        "cassettes_on_disk": _cassette_count(),
        "config": {
            "approval_threshold_usd": cfg.APPROVAL_THRESHOLD_USD,
            "near_threshold_band": cfg.NEAR_THRESHOLD_BAND,
            "price_tolerance": cfg.PRICE_TOLERANCE,
            "terms_tolerance_days": cfg.TERMS_TOLERANCE_DAYS,
            "fx_rates": dict(cfg.FX_RATES),
            "fx_rates_as_of": "2026-07-28",
            "max_critic_rounds": cfg.MAX_CRITIC_ROUNDS,
            "max_repair_attempts": cfg.MAX_REPAIR_ATTEMPTS,
            "human_gate_mode": os.environ.get("HUMAN_GATE_MODE", cfg.HUMAN_GATE_MODE),
        },
    }


def format_manifest_lines(m: dict[str, Any]) -> list[str]:
    """Human-readable version of the manifest for the CLI header."""
    git_sha = m["git"]["sha"] or "(no git)"
    dirty = " +dirty" if m["git"]["dirty"] else ""
    model = m['llm']['model'] or "(unset — set GROK_MODEL)"
    return [
        f"model={model}  mode={m['llm']['mode']}  "
        f"cassettes={m['cassettes_on_disk']}  git={git_sha}{dirty}",
        f"threshold=${m['config']['approval_threshold_usd']:,.0f}  "
        f"near-band={m['config']['near_threshold_band']:.0%}  "
        f"price-tol=±{m['config']['price_tolerance']:.0%}  "
        f"terms-tol=±{m['config']['terms_tolerance_days']}d  "
        f"fx={m['config']['fx_rates']} (as of {m['config']['fx_rates_as_of']})  "
        f"human-gate={m['config']['human_gate_mode']}",
    ]


def build_run_record(
    *,
    invoice_path: str,
    invoice_number: str,
    outcome: str,
    findings: list[Any],
    nodes_fired: list[str],
    model_calls: list[Any],
    tool_calls: list[Any],
    scribe_note: str | None,
    elapsed_seconds: float,
    terminal_status: str,
    failure_reason: str | None,
    human_outcome: str | None,
    human_note: str | None,
    settlement_result: str | None,
    mock_payment_reference: str | None,
) -> dict[str, Any]:
    """Compose the per-invoice JSONL record from graph-run outputs."""
    return {
        "type": "run",
        "invoice_path": invoice_path,
        "invoice_number": invoice_number,
        "outcome": outcome,
        "terminal_status": terminal_status,
        "failure_reason": failure_reason,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "finding_codes": [f.code for f in findings],
        "nodes_fired": list(nodes_fired),
        "model_calls": [
            {
                "prompt_name": mc.prompt_name,
                "prompt_tokens": mc.prompt_tokens,
                "cached_prompt_tokens": mc.cached_prompt_tokens,
                "completion_tokens": mc.completion_tokens,
                "reasoning_tokens": mc.reasoning_tokens,
                "cost_usd": float(mc.cost_usd),
                "latency_ms": mc.latency_ms,
            }
            for mc in model_calls
        ],
        "tool_calls": [
            {
                "name": tc.name,
                "arguments": tc.arguments,
                "latency_ms": tc.latency_ms,
            }
            for tc in tool_calls
        ],
        "scribe_note": scribe_note,
        "human_outcome": human_outcome,
        "human_note": human_note,
        "settlement_result": settlement_result,
        "mock_payment_reference": mock_payment_reference,
    }


def write_jsonl(manifest: dict[str, Any], records: list[dict[str, Any]],
                path: Path | None = None) -> Path:
    """Write manifest + records to a jsonl file. Returns the path written."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        ts = manifest["timestamp"].replace(":", "").replace("-", "").split(".")[0]
        path = RUNS_DIR / f"batch-{ts}.jsonl"
    with path.open("w") as f:
        f.write(json.dumps(manifest, default=str) + "\n")
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")
    return path
