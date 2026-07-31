"""Manifest + JSONL emission — deterministic where it needs to be, complete
where it needs to be, and never coupled to the LLM request path."""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.observability import (
    build_manifest,
    build_run_record,
    format_manifest_lines,
    write_jsonl,
)


def test_manifest_has_required_fields():
    m = build_manifest()
    assert m["type"] == "manifest"
    assert "timestamp" in m
    assert m["llm"]["model"]
    assert m["llm"]["mode"] in {"live", "replay", "auto"}
    assert "cassettes_on_disk" in m
    assert m["config"]["approval_threshold_usd"] == 10_000
    assert m["config"]["price_tolerance"] == 0.05
    assert m["config"]["terms_tolerance_days"] == 2
    assert m["config"]["fx_rates"] == {"EUR": 1.14}
    assert m["config"]["fx_rates_as_of"] == "2026-07-28"


def test_manifest_cli_lines_have_no_timestamp():
    """CLI header must be deterministic run-to-run (see byte-identical demo
    check) so the timestamp is intentionally NOT in format_manifest_lines."""
    m = build_manifest()
    lines = format_manifest_lines(m)
    joined = "\n".join(lines)
    # ISO-8601 timestamp fragments must be absent from what prints to stdout
    assert m["timestamp"] not in joined
    assert "T" + m["timestamp"].split("T")[1] not in joined


def test_run_record_serializes_findings_model_calls_tool_calls():
    from src.schema import Finding, ModelCall, Severity, ToolCall
    from datetime import datetime, timezone

    finding = Finding(
        code="TM-001", severity=Severity.MEDIUM,
        message="mock", evidence="none", field_path="terms",
    )
    mc = ModelCall(
        requested_model="grok-4.5", resolved_model="grok-4.5",
        prompt_name="adjudicator", prompt_tokens=100, cached_prompt_tokens=0,
        completion_tokens=20, reasoning_tokens=10, latency_ms=1.0,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    tc = ToolCall(
        name="get_vendor_record", arguments={"name": "X"}, result={"found": True},
        latency_ms=1.5, timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    rec = build_run_record(
        invoice_path="data/invoices/x.json",
        invoice_number="INV-0001",
        outcome="APPROVE",
        findings=[finding],
        nodes_fired=["triage", "validate"],
        model_calls=[mc],
        tool_calls=[tc],
        scribe_note=None,
        elapsed_seconds=0.123,
        terminal_status="APPROVE",
        failure_reason=None,
        human_outcome=None,
        human_note=None,
        settlement_result="PAID",
        mock_payment_reference="MOCK-ABC",
    )
    assert rec["finding_codes"] == ["TM-001"]
    assert rec["model_calls"][0]["prompt_name"] == "adjudicator"
    assert rec["tool_calls"][0]["name"] == "get_vendor_record"
    assert rec["settlement_result"] == "PAID"
    assert rec["elapsed_seconds"] == 0.123
    # Must be JSON-serializable end-to-end
    json.dumps(rec, default=str)


def test_write_jsonl_produces_manifest_first_then_records(tmp_path):
    manifest = build_manifest()
    records = [
        {"type": "run", "invoice_number": "INV-1", "outcome": "APPROVE"},
        {"type": "run", "invoice_number": "INV-2", "outcome": "REJECT"},
    ]
    path = write_jsonl(manifest, records, path=tmp_path / "batch.jsonl")
    lines = path.read_text().splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["type"] == "manifest"
    second = json.loads(lines[1])
    assert second["invoice_number"] == "INV-1"
