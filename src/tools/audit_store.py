"""Backward-compatible shim. The audit store lives at src.store.audit now.

Kept here so external imports (tests, tool modules) don't need to change.
Delete this shim in a future cleanup pass when nothing imports from here.
"""
from src.store.audit import AuditStore, PriorSettlement, RunHistoryRow

__all__ = ["AuditStore", "PriorSettlement", "RunHistoryRow"]

# Convenience: the default path constant callers may reference
from src.config import AUDIT_DB_PATH as DEFAULT_AUDIT_DB  # noqa: E402
