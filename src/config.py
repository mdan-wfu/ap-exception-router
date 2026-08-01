"""All domain constants. Loaded from env where appropriate. No logic here."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_REPO_ROOT = Path(__file__).resolve().parents[1]

# LLM provider
XAI_API_KEY: str = os.environ.get("XAI_API_KEY", "")
# Default matches the model cassettes were recorded against, so a cold clone
# (no .env, replay mode) hits the same fingerprint. Cassette keys include the
# model identifier; if this default drifts, prior cassettes go unreachable.
# `... or "grok-4.5"` (not `os.environ.get(..., "grok-4.5")`) so an explicit
# empty value in .env (`GROK_MODEL=`) falls through to the default instead of
# being interpreted as "the model is the empty string". python-dotenv sets
# blank keys as "", which os.environ.get treats as present-and-empty, which
# reached the API as `Model not found: ''`.
GROK_MODEL: str = os.environ.get("GROK_MODEL") or "grok-4.5"
# One of: "live" (always call), "replay" (never call; miss is fatal), "auto"
# (replay on hit, live on miss). Default "replay" so a cold clone with no
# .env and no explicit env-var never accidentally spends money — matches
# the CLI default in main.py (see DECISIONS 2026-07-31 default-cli-mode)
# and the dashboard's force-replay guard. Same empty-string safety as
# GROK_MODEL above.
LLM_MODE: str = os.environ.get("LLM_MODE") or "replay"

# Pricing — USD per million tokens for grok-4.5. Derived from console billing
# on 2026-07-29; confirm against xAI's published pricing page before Phase 8.
# Reasoning tokens are billed at the OUTPUT rate (empirically confirmed
# 2026-07-29 by comparing total_tokens vs prompt+completion+reasoning).
PRICE_PER_1M_INPUT: float = 2.00
# Conservative stand-in until the console-confirmed cached rate is known:
# use the full input rate. Cached tokens are always cheaper than uncached in
# practice, so charging them at the uncached rate can only OVERstate cost —
# never understate. Swap in the real rate once confirmed.
PRICE_PER_1M_CACHED_INPUT: float = PRICE_PER_1M_INPUT
PRICE_PER_1M_OUTPUT: float = 6.00

# Financial thresholds
APPROVAL_THRESHOLD_USD: float = 10_000
NEAR_THRESHOLD_BAND: float = 0.05      # flag invoices within 5% below threshold
PRICE_TOLERANCE: float = 0.05          # ±5% against reference unit price
TERMS_TOLERANCE_DAYS: int = 2

# Currency — static by design, as of 2026-07-28
FX_RATES: dict[str, float] = {"EUR": 1.14}

# Critic / repair loop limits
# Single-critic-round policy: a second round would re-issue the same challenge
# against the same invoice+findings+tools with the only new input being the
# revised rationale. That is a re-roll, not a new challenge. See DECISIONS.md
# for the reasoning and the future-work note on giving round 2 a different job.
MAX_CRITIC_ROUNDS: int = 1
MAX_REPAIR_ATTEMPTS: int = 2

# Audit store — separate DB from reference.db, gitignored via *.db pattern,
# rebuilt idempotently by `make audit-reset`.
AUDIT_DB_PATH: Path = _REPO_ROOT / "runs" / "audit.sqlite"

# Human gate mode. How the graph handles ESCALATE outcomes.
#   "interactive" — pause on interrupt(), prompt stdin, resume
#   "demo"        — auto-resolve from HUMAN_GATE_FIXTURE_PATH so `make demo`
#                   never hangs
#   "queue"       — record the escalation to the audit store and exit
#                   without resuming (Phase 11 dashboard reads the queue)
HUMAN_GATE_MODE: str = os.environ.get("HUMAN_GATE_MODE", "interactive")
HUMAN_GATE_FIXTURE_PATH: Path = _REPO_ROOT / "data" / "fixtures" / "human_gate.json"
