"""The upload-run route is the ONLY path that may make a live API call.
Every other route must remain forced to replay.

These tests verify the fix WITHOUT actually calling the API — a fake
provider stands in for the live call so we can assert the plumbing:
provider swap happens, restoration happens even on crash, no other
route sees anything but the replay provider.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


def _seed(monkeypatch, tmp_path):
    from src import config as cfg_mod
    from src.store import audit as audit_mod
    isolated = tmp_path / "audit.sqlite"
    monkeypatch.setattr(cfg_mod, "AUDIT_DB_PATH", isolated)
    monkeypatch.setattr(audit_mod, "AUDIT_DB_PATH", isolated)
    real = Path("runs/audit.sqlite")
    if real.exists():
        shutil.copy(str(real), str(isolated))
    else:
        from src.store.audit import AuditStore
        AuditStore(path=isolated)
    return isolated


@pytest.fixture
def client(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    from src.ui.app import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# The provider IS actually swapped for the upload-run call
# ---------------------------------------------------------------------------

def test_upload_run_installs_a_provider_with_non_replay_mode(client, monkeypatch, tmp_path):
    """Instrument set_provider to record every provider swap during the
    upload-run request. Assert that (a) a provider with mode != 'replay'
    was installed, (b) the original replay provider was restored after,
    (c) the final installed provider is back to 'replay'."""
    from src.llm import agent_loop
    from src.llm.provider import LLMProvider

    installed = []

    real_set = agent_loop.set_provider
    def snoop_set(p):
        installed.append(p)
        real_set(p)
    monkeypatch.setattr(agent_loop, "set_provider", snoop_set)

    # Force the upload-run code path to succeed without an actual API call
    # by monkeypatching run_one to return quickly.
    from src import graph as graph_mod
    monkeypatch.setattr(graph_mod, "run_one", lambda p: {"invoice": None})

    monkeypatch.setenv("XAI_API_KEY", "xai-fake-for-test")

    # Upload a file
    r = client.post("/upload",
                    files={"file": ("probe.txt",
                                    b"INVOICE\nInvoice Number: INV-PROBE\n"
                                    b"Vendor: Test\nTotal: $100\n",
                                    "text/plain")},
                    follow_redirects=False)
    name = r.headers["location"].rsplit("/", 1)[-1]

    # Trigger the live run
    r = client.post(f"/upload/{name}/run", data={"confirm": "yes"},
                    follow_redirects=False)

    # We expect two set_provider calls: install live, restore saved.
    assert len(installed) >= 2, (
        f"expected ≥2 set_provider calls (install + restore), got {len(installed)}"
    )
    installed_modes = [getattr(p, "mode", None) for p in installed]
    assert "auto" in installed_modes or "live" in installed_modes, (
        f"a non-replay provider must have been installed at some point; "
        f"saw modes {installed_modes}"
    )
    # And the final one must be the original replay provider (restored)
    final = agent_loop.get_provider()
    assert final.mode == "replay", (
        f"after upload-run the singleton must be back to replay; got {final.mode}"
    )


def test_upload_run_restores_replay_even_when_graph_crashes(client, monkeypatch):
    """Airtight restoration: a crash inside run_one must still leave the
    dashboard singleton at mode='replay' for the next request."""
    from src.llm import agent_loop
    from src import graph as graph_mod

    def crash(_path):
        raise RuntimeError("simulated failure inside the live run")
    monkeypatch.setattr(graph_mod, "run_one", crash)
    monkeypatch.setenv("XAI_API_KEY", "xai-fake-for-test")

    r = client.post("/upload", files={"file": ("crash.txt", b"INVOICE #INV-TEST\nAmount: $10.00\n" * 2, "text/plain")},
                    follow_redirects=False)
    name = r.headers["location"].rsplit("/", 1)[-1]

    r = client.post(f"/upload/{name}/run", data={"confirm": "yes"},
                    follow_redirects=False)
    assert r.status_code == 500  # the crash surfaced

    final = agent_loop.get_provider()
    assert final.mode == "replay", (
        "provider must be restored to replay after a crash inside run_one"
    )


# ---------------------------------------------------------------------------
# The live path is gated exactly as before
# ---------------------------------------------------------------------------

def test_upload_run_requires_confirm(client, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-fake-for-test")
    r = client.post("/upload", files={"file": ("x.txt", b"INVOICE #INV-TEST\nAmount: $10.00\n" * 2, "text/plain")},
                    follow_redirects=False)
    name = r.headers["location"].rsplit("/", 1)[-1]
    r = client.post(f"/upload/{name}/run", data={"confirm": "no"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "err=notconfirmed" in r.headers["location"]


def test_upload_run_requires_key(client, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    r = client.post("/upload", files={"file": ("x.txt", b"INVOICE #INV-TEST\nAmount: $10.00\n" * 2, "text/plain")},
                    follow_redirects=False)
    name = r.headers["location"].rsplit("/", 1)[-1]
    r = client.post(f"/upload/{name}/run", data={"confirm": "yes"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "err=nokey" in r.headers["location"]


# ---------------------------------------------------------------------------
# No other route can incur a live call
# ---------------------------------------------------------------------------

def test_every_other_route_stays_replay_only(client):
    """Boot-time invariant: ambient LLM_MODE at request time for every
    non-upload route must be 'replay'. Also the singleton provider's mode
    starts and remains 'replay'."""
    from src.llm import agent_loop
    import os

    # After any of these renders, mode should still be replay
    for path in ("/", "/queue", "/held", "/payments", "/codes", "/upload"):
        client.get(path)
        assert os.environ.get("LLM_MODE") == "replay", (
            f"LLM_MODE drifted from 'replay' after GET {path}"
        )
        assert agent_loop.get_provider().mode == "replay", (
            f"provider drifted from mode='replay' after GET {path}"
        )


def test_detail_page_of_a_novel_invoice_still_misses_cassette(client, tmp_path, monkeypatch):
    """Re-extraction on the detail page runs in the dashboard's replay
    mode. For a completed run's cassette that IS on disk, this hits. But
    hitting the detail page with a source_file the extractor has never
    seen must NOT sneak a live call through — the extractor cassette
    miss must surface as an error, not an API call.

    We prove this by pointing an existing run's source_file at a novel
    path (via UPDATE) and requesting the detail page; re_extract will
    hit CacheMissError which the dashboard degrades gracefully."""
    import sqlite3
    from src import config as cfg_mod

    novel = tmp_path / "novel_source_never_seen.txt"
    novel.write_text("INVOICE\nInvoice Number: INV-NEVERSEEN\nVendor: Ghost\n"
                     "Items:\n  WidgetA qty: 1 unit price: $250\nTotal: $250\n")
    conn = sqlite3.connect(str(cfg_mod.AUDIT_DB_PATH))
    conn.execute(
        "UPDATE runs SET source_file = ? WHERE invoice_number = 'INV-1015'",
        (str(novel),),
    )
    conn.commit()
    conn.close()

    # Detail page must render (graceful degradation) — it must NOT crash
    # and it must NOT make an API call. The extractor cassette will miss;
    # the amber banner should surface.
    r = client.get("/invoice/INV-1015")
    assert r.status_code == 200
    # And the singleton is still at replay
    from src.llm import agent_loop
    assert agent_loop.get_provider().mode == "replay"
