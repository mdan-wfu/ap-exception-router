"""Run every validator against every corpus invoice; print findings.

    python scripts/validate_corpus.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.adapters.router import extract  # noqa: E402
from src.store.seed import seed  # noqa: E402
from src.validators import Reference, find_duplicates, has_critical, run_validators  # noqa: E402

CORPUS = REPO_ROOT / "data" / "invoices"


def main() -> int:
    # Ensure a fresh reference.db
    seed()
    ref = Reference()

    # Extract every file
    all_invoices = []
    per_file = []
    for path in sorted(CORPUS.iterdir()):
        if path.suffix.lower() not in {".txt", ".pdf", ".json", ".csv", ".xml"}:
            continue
        r = extract(path)
        all_invoices.append(r.invoice)
        per_file.append((path.name, r.invoice))

    # Per-invoice findings
    findings_by_file: dict[str, list] = {}
    for name, inv in per_file:
        findings_by_file[name] = run_validators(inv, ref)

    # Batch duplicate findings
    for inv, finding in find_duplicates(all_invoices):
        # Find the file that produced this invoice
        for name, i in per_file:
            if i is inv:
                findings_by_file[name].append(finding)
                break

    # Print
    for name, inv in per_file:
        fs = findings_by_file[name]
        critical = has_critical(fs)
        by_code = defaultdict(int)
        for f in fs:
            by_code[f"{f.code}({f.severity.value[:4]})"] += 1
        codes_str = ", ".join(f"{k}×{v}" if v > 1 else k for k, v in sorted(by_code.items()))
        marker = " [CRITICAL]" if critical else ""
        print(f"{name:32s} {inv.invoice_number:10s} {len(fs):2d} findings  {codes_str}{marker}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
