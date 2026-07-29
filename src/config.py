"""All domain constants. Loaded from env where appropriate. No logic here."""
import os
from dotenv import load_dotenv

load_dotenv()

# LLM provider
XAI_API_KEY: str = os.environ.get("XAI_API_KEY", "")
GROK_MODEL: str = os.environ.get("GROK_MODEL", "")
LLM_MODE: str = os.environ.get("LLM_MODE", "replay")

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
