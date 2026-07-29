"""Triage router: extension → adapter, with LLM fallback.

Fallback fires when:
  - the deterministic adapter raises, OR
  - field coverage (see _common.field_coverage) is below MIN_FIELD_COVERAGE.

Both cases are recorded on the returned Invoice via `notes` and, in the
Phase 5 run record, as an explicit trace step. A silent degrade from
deterministic to LLM extraction is exactly the kind of behaviour that
makes cost overruns invisible.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.adapters import csv_adapter, json_adapter, pdf_adapter, text_adapter, xml_adapter
from src.adapters._common import field_coverage
from src.config import MIN_FIELD_COVERAGE
from src.schema import Invoice


@dataclass
class ExtractionResult:
    invoice: Invoice
    adapter_used: str            # "json" | "csv" | "xml" | "text" | "pdf"
    llm_fallback: bool           # deterministic path fell back to LLM
    fallback_reason: str | None  # "parse_error" | "low_field_coverage" | None


_DETERMINISTIC = {
    ".json": ("json", json_adapter.extract),
    ".csv":  ("csv",  csv_adapter.extract),
    ".xml":  ("xml",  xml_adapter.extract),
}
_LLM_NATIVE = {
    ".txt": ("text", text_adapter.extract),
    ".pdf": ("pdf",  pdf_adapter.extract),
}


def extract(path: Path) -> ExtractionResult:
    ext = path.suffix.lower()

    if ext in _LLM_NATIVE:
        adapter_name, fn = _LLM_NATIVE[ext]
        return ExtractionResult(
            invoice=fn(path),
            adapter_used=adapter_name,
            llm_fallback=False,
            fallback_reason=None,
        )

    if ext not in _DETERMINISTIC:
        raise ValueError(f"Unsupported format: {ext} ({path})")

    adapter_name, fn = _DETERMINISTIC[ext]
    try:
        inv = fn(path)
    except Exception as exc:
        return _fall_back(path, adapter_name, f"parse_error: {type(exc).__name__}: {exc}")

    coverage = field_coverage(inv)
    if coverage < MIN_FIELD_COVERAGE:
        return _fall_back(
            path,
            adapter_name,
            f"low_field_coverage: {coverage:.2f} < {MIN_FIELD_COVERAGE}",
        )

    return ExtractionResult(
        invoice=inv, adapter_used=adapter_name,
        llm_fallback=False, fallback_reason=None,
    )


def _fall_back(path: Path, deterministic_adapter: str, reason: str) -> ExtractionResult:
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8", errors="replace")
    inv = text_adapter.extract_from_text(
        text,
        source_file=str(path),
        source_format=path.suffix.lstrip("."),
        raw_bytes=raw_bytes,
    )
    return ExtractionResult(
        invoice=inv,
        adapter_used=deterministic_adapter,
        llm_fallback=True,
        fallback_reason=reason,
    )
