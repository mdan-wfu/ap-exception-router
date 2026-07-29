"""PDF adapter: pdfplumber → text → text_adapter.

Phase 1's forensics confirmed pdfplumber preserves INV-1013's 8-line table
cleanly, so no special table-mode configuration is needed. If a future
PDF proves that assumption wrong, add table extraction here — do not
teach the extractor prompt to handle mangled tables (that would let a
pdfplumber regression hide inside prompt tuning).
"""
from __future__ import annotations

from pathlib import Path

import pdfplumber

from src.adapters import text_adapter
from src.schema import Invoice


def extract(path: Path) -> Invoice:
    raw_bytes = path.read_bytes()
    text = _to_text(path)
    return text_adapter.extract_from_text(
        text,
        source_file=str(path),
        source_format="pdf",
        raw_bytes=raw_bytes,
    )


def _to_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)
