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
PRICE_PER_1M_CACHED_INPUT: float = 0.50  # TODO(pricing): confirm from console
PRICE_PER_1M_OUTPUT: float = 6.00

# Financial thresholds
APPROVAL_THRESHOLD_USD: float = 10_000
NEAR_THRESHOLD_BAND: float = 0.05      # flag invoices within 5% below threshold
PRICE_TOLERANCE: float = 0.05          # ±5% against reference unit price
TERMS_TOLERANCE_DAYS: int = 2

# Currency — static by design, as of 2026-07-28
FX_RATES: dict[str, float] = {"EUR": 1.14}

# Critic / repair loop limits
MAX_CRITIC_ROUNDS: int = 2
MAX_REPAIR_ATTEMPTS: int = 2
