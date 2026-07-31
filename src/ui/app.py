"""FastAPI dashboard. Read-only view over the audit store.

Boot with `make dashboard` → http://127.0.0.1:8000

Never calls the LLM. Never mutates cassettes. Re-extraction of invoice
sources runs in the same LLM_MODE=replay path the demo uses; deterministic
adapters return instantly and LLM adapters hit committed cassettes.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# Force replay mode so re-extraction never accidentally calls the API.
os.environ.setdefault("LLM_MODE", "replay")
os.environ.setdefault("HUMAN_GATE_MODE", "demo")

from src.ui import data


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="AP Exception Router — Dashboard", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# Landing / queue view
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def queue_view(request: Request):
    all_runs = data.list_runs()
    provided_summary = data.corpus_summary("provided")
    adversarial_summary = data.corpus_summary("adversarial")
    return templates.TemplateResponse(request, "queue.html", {
        "runs": all_runs,
        "provided": provided_summary,
        "adversarial": adversarial_summary,
        "provided_findings": data.findings_by_prefix("provided"),
        "adversarial_findings": data.findings_by_prefix("adversarial"),
    })


# ---------------------------------------------------------------------------
# Invoice detail
# ---------------------------------------------------------------------------

@app.get("/invoice/{invoice_number}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_number: str):
    run = data.get_run(invoice_number)
    if run is None:
        return HTMLResponse(f"<h1>{invoice_number} not found</h1>", status_code=404)
    ext = data.re_extract(run["source_file"])
    settlement = data.get_settlement(
        invoice_number, run["vendor_name"] or ""
    )
    return templates.TemplateResponse(request, "detail.html", {
        "run": run,
        "extraction": ext,
        "invoice": ext.invoice if ext else None,
        "raw_source": data.read_source_text(run["source_file"]),
        "findings": data.get_findings(run["id"]),
        "tool_calls": data.get_tool_calls(run["id"]),
        "cost_by_node": data.cost_by_node_type(run["id"]),
        "settlement": settlement,
    })


# ---------------------------------------------------------------------------
# Duplicate diff (INV-1004 flagship)
# ---------------------------------------------------------------------------

@app.get("/duplicate/{invoice_number}", response_class=HTMLResponse)
def duplicate_view(request: Request, invoice_number: str):
    entries = data.get_duplicate_pair(invoice_number)
    if len(entries) < 2:
        return HTMLResponse(
            f"<h1>{invoice_number} has no duplicate group in this corpus</h1>",
            status_code=404,
        )
    run = data.get_run(invoice_number)
    findings = data.get_findings(run["id"]) if run else []
    dp_findings = [f for f in findings if f["code"].startswith("DP-")]
    return templates.TemplateResponse(request, "duplicate.html", {
        "invoice_number": invoice_number,
        "entries": entries,
        "run": run,
        "dp_findings": dp_findings,
    })


# ---------------------------------------------------------------------------
# Human escalation queue
# ---------------------------------------------------------------------------

@app.get("/queue", response_class=HTMLResponse)
def human_queue_view(request: Request):
    return templates.TemplateResponse(request, "human_queue.html", {
        "queue": data.human_queue(),
        "resolved": [
            r for r in data.list_runs()
            if r["outcome"] == "ESCALATE" and r.get("human_outcome")
        ],
    })


@app.post("/queue/{invoice_number}")
def resolve(invoice_number: str, action: str = Form(...), note: str = Form("")):
    if action not in {"APPROVE", "REJECT", "HOLD"}:
        return RedirectResponse(url="/queue", status_code=303)
    data.record_human_decision(invoice_number, action, note)
    return RedirectResponse(url="/queue", status_code=303)
