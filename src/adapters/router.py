"""Triage router: extension → adapter, with LLM fallback.

Fallback fires ONLY when a deterministic adapter raises (parse error or
Pydantic ValidationError). Field coverage does NOT trigger fallback for
structured formats — a successful parse of a sparse-but-valid document
is authoritative. Falling back to the LLM on a legitimately sparse source
would let the model fabricate values the source deliberately omitted,
which prompts/extractor.md forbids.

LLM-native adapters (text/pdf) never "fall back" — they already are the
LLM. Low coverage from those paths surfaces in the run record for the
Adjudicator to weigh, but there is no lower layer to escalate to.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.adapters import csv_adapter, json_adapter, pdf_adapter, text_adapter, xml_adapter
from src.schema import Invoice


@dataclass
class ExtractionResult:
    invoice: Invoice
    adapter_used: str            # "json" | "csv" | "xml" | "text" | "pdf"
    llm_fallback: bool           # deterministic path raised and fell back to LLM
    fallback_reason: str | None  # "parse_error: ..." | None


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
        # A raise from a deterministic parser (json.JSONDecodeError,
        # csv.Error, xml.etree.ElementTree.ParseError, Pydantic
        # ValidationError, etc.) is the fallback trigger. A successful
        # parse — even one with low field coverage — is authoritative.
        return _fall_back(
            path, adapter_name, f"parse_error: {type(exc).__name__}: {exc}"
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
