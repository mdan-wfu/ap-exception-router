"""Integrity rails.

Rule: nothing under src/ may import from eval/. If extraction can see the
answers, the accuracy numbers are fiction.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


def test_src_never_imports_eval() -> None:
    offenders: list[str] = []
    import_pattern = re.compile(r"^\s*(from|import)\s+eval(\.|\s|$)")
    for py in SRC_ROOT.rglob("*.py"):
        for line in py.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if import_pattern.match(stripped):
                offenders.append(f"{py.relative_to(REPO_ROOT)}: {stripped}")
    assert not offenders, (
        "src/ must never import from eval/ — extraction cannot see ground truth. "
        "Offenders:\n" + "\n".join(offenders)
    )
