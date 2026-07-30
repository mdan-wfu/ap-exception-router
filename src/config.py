"""All domain constants. Loaded from env where appropriate. No logic here."""
import os
from dotenv import load_dotenv

load_dotenv()

# LLM provider
XAI_API_KEY: str = os.environ.get("XAI_API_KEY", "")
GROK_MODEL: str = os.environ.get("GROK_MODEL", "")
# One of: "live" (always call), "replay" (never call; miss is fatal), "auto"
# (replay on hit, live on miss). Default "auto" so `python main.py ...` works
# both with and without a fresh API key.
LLM_MODE: str = os.environ.get("LLM_MODE", "auto")

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
