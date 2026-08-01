"""FastAPI dashboard. Read-only view over the audit store.

Boot with `make dashboard` → http://127.0.0.1:8000

Never calls the LLM at page render. Re-extraction of invoice sources runs
in `LLM_MODE=replay`; deterministic adapters return instantly and LLM
adapters hit committed cassettes. The single explicit exception is the
`/upload/{name}/run` POST — it requires an affirmative click, a
configured XAI_API_KEY, and switches temporarily to `--live` for exactly
one invoice. See DECISIONS 2026-07-31 dashboard-forced-replay for why.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# ORDER MATTERS. src.config captures LLM_MODE at import time via
# `from src.config import LLM_MODE` in src.llm.provider. If we import
# config first and then set os.environ["LLM_MODE"]="replay", src.config's
# snapshot has already been taken with whatever the ambient value was
# (default "auto" on cold clone with no .env). The lazy provider
# singleton then reads mode="auto" and pages render with cache-miss →
# live spend — exactly the Phase 11 invariant we swore off.
# So: set the env var BEFORE importing config. load_dotenv() doesn't
# override existing env vars, so a .env with LLM_MODE=... won't clobber
# this either.
os.environ["LLM_MODE"] = "replay"
os.environ.setdefault("HUMAN_GATE_MODE", "demo")

# Now trigger config's load_dotenv() so XAI_API_KEY (if any) reaches
# data.xai_key_configured() before the first request.
from src import config as _cfg  # noqa: F401  (side-effect import)

from src.ui import data


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

def _demo_mode_ctx(request):
    """Every template sees `demo_mode` = True iff any run in the store was
    resolved by the HUMAN_GATE_MODE=demo fixture. Drives the top banner in
    base.html so per-row `auto` chips minimize to only where they actually
    distinguish. See DECISIONS 2026-07-31 demo-banner-not-per-row."""
    try:
        return {"demo_mode": data.demo_fixture_active()}
    except Exception:
        return {"demo_mode": False}

templates = Jinja2Templates(directory=str(TEMPLATES_DIR),
                             context_processors=[_demo_mode_ctx])

app = FastAPI(title="AP Exception Router — Dashboard", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# Queue view (landing)
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def queue_view(request: Request, sort: str = "due_date"):
    all_runs = data.list_runs()
    worklist = [r for r in all_runs if r["is_awaiting"] or r["is_held"]]
    settled = [r for r in all_runs if r["is_resolved"]]
    failed = [r for r in all_runs if r["is_failed"]]
    # Urgency sort for the worklist; alphabetical for the settled section.
    worklist.sort(key=lambda r: data.queue_sort_key(r, mode=sort))
    settled.sort(key=lambda r: r["invoice_number"])
    failed.sort(key=lambda r: r["invoice_number"])
    return templates.TemplateResponse(request, "queue.html", {
        "active_nav": "queue",
        "worklist": worklist,
        "settled": settled,
        "failed": failed,
        "sort": sort,
        "progress": data.queue_progress(all_runs),
        "provided": data.corpus_summary("provided"),
        "adversarial": data.corpus_summary("adversarial"),
        "provided_findings": data.findings_by_prefix("provided"),
        "adversarial_findings": data.findings_by_prefix("adversarial"),
        "code_summaries": data.FINDING_SUMMARIES,
    })


# ---------------------------------------------------------------------------
# Invoice detail
# ---------------------------------------------------------------------------

@app.get("/invoice/{invoice_number}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_number: str):
    run = data.get_run(invoice_number)
    if run is None:
        return HTMLResponse(f"<h1>{invoice_number} not found</h1>", status_code=404)
    ext, extraction_error = data.re_extract(run["source_file"])
    settlement = data.get_settlement(invoice_number, run["vendor_name"] or "")
    return templates.TemplateResponse(request, "detail.html", {
        "active_nav": "queue",
        "run": run,
        "extraction": ext,
        "extraction_error": extraction_error,
        "invoice": ext.invoice if ext else None,
        "raw_source": data.read_source_text(run["source_file"]),
        "findings": data.get_findings(run["id"]),
        "tool_calls": data.get_tool_calls(run["id"]),
        "cost_by_node": data.cost_by_node_type(run["id"]),
        "settlement": settlement,
        "history": data.decision_history(invoice_number),
        "effective_outcome": data.effective_outcome(invoice_number),
        "auto_resolved": data._is_auto_resolved(run.get("human_note")),
        "code_summaries": data.FINDING_SUMMARIES,
        "reason_callout": data.current_hold_or_amendment_reason(invoice_number),
    })


@app.get("/payments", response_class=HTMLResponse)
def payments_view(request: Request):
    return templates.TemplateResponse(request, "payments.html", {
        "active_nav": "payments",
        "ledger": data.payments_ledger(),
        "totals": data.payments_total(),
    })


@app.post("/invoice/{invoice_number}/amend")
def amend_decision(
    invoice_number: str,
    new_outcome: str = Form(...),
    reason: str = Form(...),
):
    if new_outcome not in {"APPROVE", "REJECT", "HOLD"}:
        return RedirectResponse(url=f"/invoice/{invoice_number}", status_code=303)
    if not reason.strip():
        return RedirectResponse(url=f"/invoice/{invoice_number}?err=reason_required",
                                status_code=303)
    data.record_amendment(invoice_number, new_outcome, reason.strip())
    return RedirectResponse(url=f"/invoice/{invoice_number}", status_code=303)


# ---------------------------------------------------------------------------
# Duplicate diff (INV-1004)
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
        "active_nav": "queue",
        "invoice_number": invoice_number,
        "entries": entries,
        "run": run,
        "dp_findings": dp_findings,
    })


# ---------------------------------------------------------------------------
# Human review tabs — awaiting, held, resolved
# ---------------------------------------------------------------------------

@app.get("/queue", response_class=HTMLResponse)
def human_queue_view(request: Request):
    resolved = data.resolved_queue()
    return templates.TemplateResponse(request, "human_queue.html", {
        "active_nav": "review",
        "awaiting": data.human_queue(),
        "resolved": resolved,
        # Per-row `source` column only when the table mixes fixture + real
        # decisions. Otherwise the top banner carries it.
        "mixed": data.has_mixed_decisions(resolved),
    })


@app.get("/held", response_class=HTMLResponse)
def held_view(request: Request):
    return templates.TemplateResponse(request, "held.html", {
        "active_nav": "held",
        "held": data.held_queue(),
    })


@app.post("/queue/{invoice_number}")
def resolve(invoice_number: str, action: str = Form(...), note: str = Form("")):
    if action not in {"APPROVE", "REJECT", "HOLD"}:
        return RedirectResponse(url="/queue", status_code=303)
    data.record_human_decision(invoice_number, action, note)
    # HOLDs go to the Held tab; APPROVE/REJECT to the resolved section of /queue.
    return RedirectResponse(
        url="/held" if action == "HOLD" else "/queue",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Code legend (B4)
# ---------------------------------------------------------------------------

@app.get("/codes", response_class=HTMLResponse)
def codes_view(request: Request):
    return templates.TemplateResponse(request, "codes.html", {
        "active_nav": "codes",
        "codes": data.finding_code_legend(),
    })


# ---------------------------------------------------------------------------
# Upload (B8) — the ONE dashboard path that can authorize a live call
# ---------------------------------------------------------------------------

@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request):
    err = request.query_params.get("err")
    err_message = {
        "nofile": "No file or pasted text was received.",
        "empty": "That file is empty. Upload a document with content.",
        "too_small": "That file is smaller than a plausible invoice (< 32 bytes). Upload the real source.",
        "unsupported": "Unsupported extension. Accepted: .txt .pdf .json .csv .xml",
        "unreadable": "That file is not readable as text or PDF. Check the source.",
    }.get(err or "") if err else None
    uploads = _list_uploads()
    recent_limit = 5
    return templates.TemplateResponse(request, "upload.html", {
        "active_nav": "upload",
        "key_configured": data.xai_key_configured(),
        "uploads": uploads,
        "recent_uploads": uploads[:recent_limit],
        "older_uploads": uploads[recent_limit:],
        "err_message": err_message,
    })


@app.post("/upload")
async def upload_receive(
    request: Request,
    file: UploadFile | None = File(None),
    pasted: str = Form(""),
    pasted_name: str = Form("pasted_invoice.txt"),
):
    if file is not None and file.filename:
        contents = await file.read()
        filename = file.filename
    elif pasted.strip():
        contents = pasted.encode("utf-8")
        filename = pasted_name
    else:
        return RedirectResponse(url="/upload?err=nofile", status_code=303)

    reject = data.reject_upload(filename, contents)
    if reject is not None:
        return RedirectResponse(url=f"/upload?err={reject}", status_code=303)

    dest = data.save_upload(filename, contents)
    return RedirectResponse(url=f"/upload/{dest.name}", status_code=303)


@app.post("/upload/{name}/delete")
def upload_delete(name: str):
    """Remove a single uploaded file. Sanitized against path traversal
    in data.remove_upload; a name not resolving inside uploads_dir is a
    no-op."""
    data.remove_upload(name)
    return RedirectResponse(url="/upload", status_code=303)


@app.get("/upload/{name}", response_class=HTMLResponse)
def upload_detail(request: Request, name: str):
    path = data.uploads_dir() / name
    if not path.exists():
        return HTMLResponse(f"<h1>upload {name!r} not found</h1>", status_code=404)
    preview = _preview_bytes(path)
    return templates.TemplateResponse(request, "upload_detail.html", {
        "active_nav": "upload",
        "filename": name,
        "path": str(path),
        "size": path.stat().st_size,
        "preview": preview,
        "key_configured": data.xai_key_configured(),
        "looks_like_invoice": data.looks_like_invoice(preview),
    })


@app.post("/upload/{name}/run")
def upload_run(name: str, confirm: str = Form("")):
    """The ONLY dashboard path that can incur cost. Gated on both an
    affirmative `confirm=yes` from the button and a configured
    XAI_API_KEY. Runs exactly ONE invoice, live."""
    path = data.uploads_dir() / name
    if not path.exists():
        return HTMLResponse(f"<h1>upload {name!r} not found</h1>", status_code=404)
    if confirm != "yes":
        return RedirectResponse(url=f"/upload/{name}?err=notconfirmed", status_code=303)
    if not data.xai_key_configured():
        return RedirectResponse(url=f"/upload/{name}?err=nokey", status_code=303)

    # The env-var flip (previous code) was a no-op: src/config.py reads
    # LLM_MODE once at import time, so `os.environ[...] = "auto"` never
    # reached the provider. The bug: clicking Run live raised CacheMissError.
    #
    # Fix: swap the agent_loop provider singleton with an explicit
    # `mode="auto"` one for exactly this call, restore in `finally` so
    # any crash mid-request still returns the dashboard to replay-only.
    # Approach A (from the patch options) — thread the mode through by
    # constructing a provider that has it — because approach B (env-var
    # rewrite + module reload) doesn't work with our from-import.
    #
    # See DECISIONS 2026-07-31 upload-run-live-provider-swap.
    # TWO provider singletons live under src.llm — the agent_loop's (used
    # by adjudicate/critic/scribe) and text_adapter's (used by the .txt/.pdf
    # extractor). Both must be swapped for a live run; missing either means
    # the extraction step still cache-misses in replay mode.
    from src.llm.agent_loop import get_provider as get_agent_provider, \
                                     set_provider as set_agent_provider
    from src.adapters.text_adapter import _get_provider as get_text_provider, \
                                            set_provider as set_text_provider
    from src.llm.provider import LLMProvider
    from src.llm.cassette import CassetteStore

    saved_agent = get_agent_provider()
    saved_text = get_text_provider()
    live_agent = LLMProvider(mode="auto", cassette_store=CassetteStore())
    live_text = LLMProvider(mode="auto", cassette_store=CassetteStore())
    set_agent_provider(live_agent)
    set_text_provider(live_text)
    from src.schema import Outcome
    try:
        from src.graph import run_one
        state = run_one(str(path))
    finally:
        # Airtight restoration: even if the run raised or returned FAILED,
        # both singletons return to replay before the next request lands.
        set_agent_provider(saved_agent)
        set_text_provider(saved_text)

    # A FAILED terminal_status means run_one caught an exception and wrote
    # a FAILED audit row keyed on the upload's basename. Send the user to
    # that record — they see the failure reason surfaced next to a normal
    # invoice detail page, not a bare 500.
    if state.get("terminal_status") == Outcome.FAILED:
        return RedirectResponse(url=f"/invoice/{path.name}", status_code=303)

    # Redirect to the invoice detail — the audit store now has a row.
    ext, _ = data.re_extract(str(path))
    if ext is not None:
        return RedirectResponse(url=f"/invoice/{ext.invoice.invoice_number}",
                                status_code=303)
    return RedirectResponse(url="/", status_code=303)


def _list_uploads() -> list[dict]:
    """Uploads sorted most-recent-first by mtime. Skips .gitkeep (present
    so `data/uploads/` survives a clone)."""
    d = data.uploads_dir()
    entries = [
        {"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime}
        for p in d.iterdir() if p.is_file() and p.name != ".gitkeep"
    ]
    entries.sort(key=lambda x: x["mtime"], reverse=True)
    return entries


def _preview_bytes(path: Path, limit: int = 4000) -> str:
    try:
        if path.suffix.lower() == ".pdf":
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                txt = "\n".join(p.extract_text() or "" for p in pdf.pages)
        else:
            txt = path.read_text(errors="replace")
    except Exception as exc:
        return f"(cannot preview: {type(exc).__name__}: {exc})"
    return txt[:limit] + ("\n… (truncated)" if len(txt) > limit else "")
