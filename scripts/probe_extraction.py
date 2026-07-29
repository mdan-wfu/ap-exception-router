"""
Extraction baseline — deliberately minimal prompt, four documents × 3 runs each.

Watching for:
  invoice_1012.txt — silent OCR correction (2O26 → 2026, $3,500.O0 → $3,500.00)
  invoice_1013.pdf — line-item collapsing (three WidgetA lines → one?)
  invoice_1003.txt — date hallucination (Due Date: yesterday → null or invented date)
  invoice_1002.txt — normalization drift (Inv #: 1002 → "1002" or "INV-1002")

This script MUST NOT be tuned. The minimal prompt is the point.
"""
import json
import os
import sys
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

API_KEY = os.environ["XAI_API_KEY"]
MODEL = os.environ["GROK_MODEL"]

client = OpenAI(api_key=API_KEY, base_url="https://api.x.ai/v1")

OUTPUT_DIR = Path("scripts/probe_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": ["string", "null"]},
        "vendor": {"type": ["string", "null"]},
        "date": {"type": ["string", "null"]},
        "due_date": {"type": ["string", "null"]},
        "total": {"type": ["number", "null"]},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "quantity": {"type": ["number", "null"]},
                    "unit_price": {"type": ["number", "null"]},
                    "amount": {"type": ["number", "null"]},
                },
                "required": ["item"],
            },
        },
        "payment_terms": {"type": ["string", "null"]},
    },
    "required": ["invoice_number", "vendor", "date", "due_date", "total", "line_items"],
}

MINIMAL_PROMPT = "Extract the invoice fields from this document as JSON."


def load_text(path: str) -> str:
    if path.endswith(".pdf"):
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    with open(path, encoding="utf-8") as f:
        return f.read()


def extract(document_text: str) -> dict:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "user", "content": f"{MINIMAL_PROMPT}\n\n{document_text}"},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "InvoiceExtraction", "schema": EXTRACTION_SCHEMA},
        },
        max_completion_tokens=1000,
    )
    raw = resp.choices[0].message.content or "{}"
    return json.loads(raw)


DOCUMENTS = [
    ("invoice_1012", "data/invoices/invoice_1012.txt"),
    ("invoice_1013", "data/invoices/invoice_1013.pdf"),
    ("invoice_1003", "data/invoices/invoice_1003.txt"),
    ("invoice_1002", "data/invoices/invoice_1002.txt"),
]

RUNS = 3


def run_baseline() -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    for name, path in DOCUMENTS:
        print(f"\n--- {name} ({path}) ---")
        doc_text = load_text(path)
        runs = []
        for run_idx in range(1, RUNS + 1):
            print(f"  Run {run_idx}/{RUNS}...", end=" ", flush=True)
            result = extract(doc_text)
            out_path = OUTPUT_DIR / f"{name}_run{run_idx}.json"
            out_path.write_text(json.dumps(result, indent=2))
            runs.append(result)
            print("done")
        results[name] = runs
    return results


def summarize(results: dict[str, list[dict]]) -> None:
    print("\n" + "=" * 70)
    print("EXTRACTION BASELINE SUMMARY")
    print("=" * 70)

    # invoice_1012: silent OCR correction
    runs = results["invoice_1012"]
    print("\n[invoice_1012] OCR artifact correction (2O26 / $3,500.O0)")
    dates = [r.get("date") for r in runs]
    totals = [r.get("total") for r in runs]
    print(f"  date values returned:  {dates}")
    print(f"  total values returned: {totals}")
    all_agree_date = len(set(str(d) for d in dates)) == 1
    all_agree_total = len(set(str(t) for t in totals)) == 1
    print(f"  date agreement across 3 runs: {'YES' if all_agree_date else 'NO — variance detected'}")
    print(f"  total agreement across 3 runs: {'YES' if all_agree_total else 'NO — variance detected'}")
    corrected_date = any(d and "2026" in str(d) and "O" not in str(d) for d in dates)
    corrected_total = any(t and abs(float(t) - 9975.0) < 1 for t in totals)
    print(f"  silently corrected date (no indication of repair): {'YES' if corrected_date else 'NO'}")
    print(f"  silently corrected total (no indication of repair): {'YES' if corrected_total else 'NO'}")

    # invoice_1013: line-item collapsing
    runs = results["invoice_1013"]
    print("\n[invoice_1013] Line-item collapsing (3 WidgetA lines)")
    for i, r in enumerate(runs, 1):
        items = r.get("line_items", [])
        widget_a_lines = [it for it in items if "widgeta" in str(it.get("item", "")).lower().replace(" ", "")]
        print(f"  Run {i}: total line items={len(items)}, WidgetA lines={len(widget_a_lines)}")
        if len(widget_a_lines) > 0:
            total_qty = sum(it.get("quantity") or 0 for it in widget_a_lines)
            print(f"          WidgetA items: {widget_a_lines}")
            print(f"          WidgetA total qty across lines: {total_qty}")
    all_item_counts = [len(r.get("line_items", [])) for r in runs]
    print(f"  Line item count per run: {all_item_counts}")
    collapsed = any(len(r.get("line_items", [])) < 8 for r in runs)
    print(f"  Collapsing occurred: {'YES' if collapsed else 'NO — all 8 lines preserved'}")

    # invoice_1003: date hallucination
    runs = results["invoice_1003"]
    print("\n[invoice_1003] Due date hallucination ('yesterday')")
    for i, r in enumerate(runs, 1):
        due = r.get("due_date")
        print(f"  Run {i}: due_date={due!r}")
    due_dates = [r.get("due_date") for r in runs]
    all_null = all(d is None for d in due_dates)
    any_null = any(d is None for d in due_dates)
    print(f"  All runs returned null: {'YES' if all_null else 'NO'}")
    print(f"  Any run returned null:  {'YES' if any_null else 'NO'}")
    print(f"  Hallucinated date:      {'YES' if not all_null else 'NO'}")

    # invoice_1002: normalization drift
    runs = results["invoice_1002"]
    print("\n[invoice_1002] Invoice number normalization ('Inv #: 1002')")
    inv_nums = [r.get("invoice_number") for r in runs]
    for i, num in enumerate(inv_nums, 1):
        print(f"  Run {i}: invoice_number={num!r}")
    all_agree = len(set(str(n) for n in inv_nums)) == 1
    print(f"  All runs agree: {'YES' if all_agree else 'NO — variance detected'}")
    normalized = any(str(n).startswith("INV-") for n in inv_nums if n)
    raw_number = any(str(n) == "1002" for n in inv_nums if n)
    print(f"  Normalized to INV-1002 format: {'YES' if normalized else 'NO'}")
    print(f"  Kept as bare '1002':           {'YES' if raw_number else 'NO'}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    print(f"Model: {MODEL}")
    print(f"Runs per document: {RUNS}")
    print(f"Output directory: {OUTPUT_DIR}")

    results = run_baseline()
    summarize(results)
    print("\nDone. Raw outputs written to scripts/probe_output/")
