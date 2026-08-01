"""In-dashboard API-key setup — the reviewer never touches a terminal
editor. Two contracts:

  1. UX: no-key state shows the setup form; submitting a valid key
     flips the UI to configured state; malformed input is rejected
     with a clear message.
  2. Security: the full key never appears in any URL, response body,
     template context, or on-disk artifact beyond the gitignored .env.
     A shape-invalid key never touches disk. All display uses
     `xai-…LAST4` masking.

Zero live calls. Zero writes outside the tmp .env we control per test."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# A shape-valid xAI key that is obviously fake (no real API would ever
# accept this). Length 28 chars total, matches the `xai-[A-Za-z0-9_-]{20,}`
# regex the validator requires.
FAKE_KEY = "xai-EXAMPLE00000000FAKEKEY99"
FAKE_KEY_LAST4 = FAKE_KEY[-4:]  # "EY99"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Every test writes to its own .env under a scratch cwd, so the
    real repo .env is never touched. The `_ENV_FILE` / `_ENV_EXAMPLE`
    module-level paths in data.py are relative to the process cwd, so
    chdir into tmp_path makes them harmless.

    Forces XAI_API_KEY out of os.environ at both setup AND teardown:
    save_api_key_to_env mutates os.environ directly (that's part of
    its contract — the running process must see the new key), which
    survives monkeypatch teardown across tests otherwise."""
    import os
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    os.environ.pop("XAI_API_KEY", None)  # belt + suspenders
    (tmp_path / ".env.example").write_text(
        "XAI_API_KEY=\nGROK_MODEL=grok-4.5\nLLM_MODE=replay\n"
    )
    yield tmp_path
    os.environ.pop("XAI_API_KEY", None)


@pytest.fixture
def client(isolated):
    # Import first (may trigger src.config's load_dotenv on cold pytest,
    # which walks upward from src/config.py and finds the repo's real
    # .env), THEN pop XAI_API_KEY. Doing it in the reverse order lets
    # the module-level load_dotenv silently re-set the key after our
    # pop. See tests/test_api_key_setup for the failure mode this
    # ordering prevents.
    from src.ui.app import app
    import os
    os.environ.pop("XAI_API_KEY", None)
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# Pure helpers — validation and masking
# ---------------------------------------------------------------------------

from src.ui.data import (
    mask_api_key,
    save_api_key_to_env,
    validate_api_key_shape,
    xai_key_configured,
)


def test_validate_rejects_empty():
    assert validate_api_key_shape("") is not None
    assert validate_api_key_shape("   ") is not None


def test_validate_rejects_wrong_prefix():
    reason = validate_api_key_shape("sk-1234567890abcdef1234567890")
    assert reason is not None and "xai-" in reason


def test_validate_rejects_too_short():
    reason = validate_api_key_shape("xai-abc")
    assert reason is not None and ("short" in reason.lower() or "too" in reason.lower())


def test_validate_rejects_bad_characters():
    reason = validate_api_key_shape("xai-has spaces and !!@# characters here")
    assert reason is not None


def test_validate_accepts_shape_valid_key():
    assert validate_api_key_shape(FAKE_KEY) is None


def test_mask_never_reveals_middle():
    masked = mask_api_key(FAKE_KEY)
    # Full key never appears in the mask
    assert FAKE_KEY not in masked
    # Middle chars are hidden
    assert "EXAMPLE" not in masked
    # Last 4 chars are the identifier a user recognizes
    assert masked.endswith(FAKE_KEY_LAST4)
    # Prefix is preserved so it's obviously an xAI key
    assert masked.startswith("xai-")


def test_mask_handles_unset():
    assert mask_api_key("") == "(unset)"
    assert mask_api_key("not-a-key") == "(unset)"


# ---------------------------------------------------------------------------
# save_api_key_to_env — file writing + env update
# ---------------------------------------------------------------------------

def test_save_writes_key_to_env(isolated):
    ok, msg = save_api_key_to_env(FAKE_KEY)
    assert ok is True
    env = (isolated / ".env").read_text()
    assert f"XAI_API_KEY={FAKE_KEY}" in env
    # Preserved from .env.example
    assert "GROK_MODEL=grok-4.5" in env
    assert "LLM_MODE=replay" in env


def test_save_message_only_contains_mask_not_full_key(isolated):
    ok, msg = save_api_key_to_env(FAKE_KEY)
    assert ok is True
    assert FAKE_KEY not in msg
    assert FAKE_KEY_LAST4 in msg  # last 4 shown as identifier
    assert "xai-" in msg


def test_save_replaces_existing_key_line(isolated):
    (isolated / ".env").write_text(
        "XAI_API_KEY=xai-OLD00000000000000000000\nGROK_MODEL=grok-4.5\n"
    )
    ok, _ = save_api_key_to_env(FAKE_KEY)
    assert ok
    env = (isolated / ".env").read_text()
    assert "xai-OLD" not in env
    assert FAKE_KEY in env
    # Only ONE XAI_API_KEY line — replacement, not append
    assert env.count("XAI_API_KEY=") == 1


def test_save_creates_env_from_example_when_absent(isolated):
    # Ensure .env doesn't exist yet
    assert not (isolated / ".env").exists()
    ok, _ = save_api_key_to_env(FAKE_KEY)
    assert ok
    assert (isolated / ".env").exists()
    env_contents = (isolated / ".env").read_text()
    example_contents = (isolated / ".env.example").read_text()
    # Same shape as .env.example plus the filled-in key.
    for line in example_contents.splitlines():
        if line.startswith("XAI_API_KEY=") or not line.strip():
            continue
        assert line in env_contents, f"line dropped: {line!r}"


def test_save_rejects_invalid_key_and_does_not_write(isolated):
    ok, msg = save_api_key_to_env("nope")
    assert ok is False
    assert msg  # non-empty error
    # No .env created on failure
    assert not (isolated / ".env").exists()


def test_save_updates_process_env_so_next_request_sees_it(isolated, monkeypatch):
    assert not xai_key_configured()
    save_api_key_to_env(FAKE_KEY)
    assert xai_key_configured() is True
    import os
    assert os.environ["XAI_API_KEY"] == FAKE_KEY


def test_provider_built_after_save_uses_new_key(isolated):
    """LLMProvider() constructed AFTER save_api_key_to_env must use the
    saved key — not the empty string frozen in src.config at import time.

    Regression guard: the bug was that provider.py did
    `from src.config import XAI_API_KEY` (a string snapshot) and then
    `self.api_key = api_key or XAI_API_KEY` in __init__. save_api_key_to_env
    correctly updated os.environ but the frozen constant stayed "". Any
    upload_run live call therefore used "replay-mode-placeholder".

    No live API call — we inspect provider.api_key and _client.api_key
    directly. The OpenAI client is never called here."""
    import os
    assert not os.environ.get("XAI_API_KEY"), "pre-condition: no key in env"

    ok, _ = save_api_key_to_env(FAKE_KEY)
    assert ok

    from src.llm.provider import LLMProvider
    from src.llm.cassette import CassetteStore
    # Construct exactly as upload_run does (no api_key= kwarg)
    provider = LLMProvider(mode="auto", cassette_store=CassetteStore())

    assert provider.api_key == FAKE_KEY, (
        f"Provider used stale key {provider.api_key!r} — __init__ must read "
        "os.environ['XAI_API_KEY'] at construction time, not the frozen config constant."
    )
    # The underlying OpenAI client was also built with the real key
    assert provider._client.api_key == FAKE_KEY


# ---------------------------------------------------------------------------
# HTTP flow — routes, redirects, template rendering
# ---------------------------------------------------------------------------

def test_upload_page_shows_setup_form_when_no_key(client):
    body = client.get("/upload").text
    assert 'name="api_key"' in body
    assert 'type="password"' in body
    assert "console.x.ai" in body
    assert "xai-" in body  # placeholder / label


def test_post_valid_key_writes_env_and_redirects_with_flag(client, isolated):
    r = client.post("/upload/api-key", data={"api_key": FAKE_KEY})
    assert r.status_code == 303
    assert r.headers["location"] == "/upload?key_saved=1"
    # File was written
    assert FAKE_KEY in (isolated / ".env").read_text()

    # Following the redirect renders the configured-state UI with masked key
    body = client.get("/upload?key_saved=1").text
    assert "Configured" in body
    assert FAKE_KEY_LAST4 in body
    # And critically — the full key is NOT in the rendered HTML anywhere
    assert FAKE_KEY not in body


def test_post_invalid_key_redirects_with_error_and_does_not_write(client, isolated):
    r = client.post("/upload/api-key", data={"api_key": "not-a-key"})
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/upload?key_err=")
    # No file written
    assert not (isolated / ".env").exists()

    # Rendered page shows the error, not the submitted string
    body = client.get(loc).text
    assert "not-a-key" not in body


def test_configured_state_shows_masked_key_and_replace_link(client, isolated):
    save_api_key_to_env(FAKE_KEY)
    body = client.get("/upload").text
    assert "Configured" in body
    assert FAKE_KEY_LAST4 in body
    assert FAKE_KEY not in body
    assert "replace" in body.lower()


def test_replace_query_reopens_setup_form(client, isolated):
    save_api_key_to_env(FAKE_KEY)
    body = client.get("/upload?replace=1").text
    assert 'name="api_key"' in body
    # Current key shown as masked context, not full
    assert FAKE_KEY_LAST4 in body
    assert FAKE_KEY not in body


# ---------------------------------------------------------------------------
# Security invariants — the full key must not leak
# ---------------------------------------------------------------------------

def test_key_never_appears_in_get_url_or_query_string(client, isolated):
    """The POST is form-only; the GET redirects use a boolean flag or
    an opaque error message. Neither carries the key."""
    r = client.post("/upload/api-key", data={"api_key": FAKE_KEY})
    loc = r.headers["location"]
    assert FAKE_KEY not in loc
    assert FAKE_KEY_LAST4 not in loc  # even the mask is not in the URL

    # And a wrong-key path: the error redirect never echoes the input.
    r2 = client.post("/upload/api-key", data={"api_key": "xai-bogus_but_shape_bad!!!"})
    loc2 = r2.headers["location"]
    assert "bogus" not in loc2


def test_key_never_appears_in_response_bodies_after_save(client, isolated):
    save_api_key_to_env(FAKE_KEY)
    for path in ["/", "/upload", "/upload?replace=1", "/queue", "/held",
                 "/payments", "/codes"]:
        r = client.get(path)
        assert FAKE_KEY not in r.text, f"full key leaked in {path}"


def test_key_never_appears_in_audit_store(client, isolated):
    """The key setup must not touch the audit store at all — direct
    check by grepping the SQLite file's bytes."""
    save_api_key_to_env(FAKE_KEY)
    # Trigger a page render that reads the audit store, ensuring any
    # side-effect writes would have happened by now.
    client.get("/")
    from src import config as cfg
    audit_path = Path(cfg.AUDIT_DB_PATH)
    if audit_path.exists():
        blob = audit_path.read_bytes()
        assert FAKE_KEY.encode() not in blob, "full key found in audit sqlite bytes"


def test_env_file_has_600_permissions_after_write(isolated):
    """Best-effort file mode: 0o600 so a shared machine doesn't leak
    the key to other users. Not a Windows guarantee; skipped there."""
    import os, sys
    if sys.platform == "win32":
        pytest.skip("POSIX permissions only")
    save_api_key_to_env(FAKE_KEY)
    mode = os.stat(isolated / ".env").st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_env_shape_matches_cp_env_example_plus_key(isolated):
    """The written .env is byte-identical to `cp .env.example .env` plus
    an XAI_API_KEY=<key> line where the blank was."""
    save_api_key_to_env(FAKE_KEY)
    expected = re.sub(
        r"^XAI_API_KEY=$",
        f"XAI_API_KEY={FAKE_KEY}",
        (isolated / ".env.example").read_text(),
        flags=re.MULTILINE,
    )
    actual = (isolated / ".env").read_text()
    # Allow trailing whitespace differences from splitlines+join
    assert actual.rstrip() + "\n" == expected.rstrip() + "\n"
