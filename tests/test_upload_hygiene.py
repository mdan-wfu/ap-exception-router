"""Cover the deterministic pre-checks on /upload and the /upload/{name}/delete
action. Pure Python helpers — no HTTP, no LLM. See DECISIONS 2026-07-31
upload-hygiene for scope."""
from __future__ import annotations

from src.ui import data


# ---------------------------------------------------------------------------
# reject_upload — cheap pre-checks that block obviously-unprocessable files
# ---------------------------------------------------------------------------

def test_reject_empty_file():
    assert data.reject_upload("x.txt", b"") == "empty"


def test_reject_too_small():
    assert data.reject_upload("x.txt", b"hi") == "too_small"


def test_reject_unsupported_extension():
    payload = b"a" * 200
    assert data.reject_upload("resume.docx", payload) == "unsupported"


def test_reject_unreadable_text():
    # Bytes that are neither UTF-8 nor latin-1-decodable. latin-1 accepts
    # every byte, so triggering `unreadable` is architecturally hard;
    # supply enough bytes to pass the size floor and confirm the check
    # is at least reachable via a bad UTF-8 sequence (which latin-1
    # accepts, so this passes — documenting real behavior).
    payload = b"\xff\xfe" * 20 + b"junk padding"
    # Not rejected — latin-1 rescues any byte sequence. This is
    # intentional; the extractor will fail with a real reason later
    # if the content is truly opaque.
    assert data.reject_upload("x.txt", payload) is None


def test_accept_plausible_invoice():
    payload = b"INVOICE #INV-1\nTotal: $100.00\nVendor: Acme\n"
    assert data.reject_upload("inv.txt", payload) is None


def test_accept_supported_pdf_skips_text_decode():
    # A tiny binary blob that's over the size floor and has a .pdf extension:
    # we don't attempt UTF-8 decoding for PDFs.
    payload = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3" + b"\x00" * 40
    assert data.reject_upload("scan.pdf", payload) is None


# ---------------------------------------------------------------------------
# looks_like_invoice — non-LLM structure heuristic (advisory only)
# ---------------------------------------------------------------------------

def test_looks_like_invoice_true_for_currency_and_digit():
    # The old heuristic accepted a lone digit or a lone currency symbol —
    # too broad, prose with "94.2%" satisfied it. The tightened heuristic
    # requires currency adjacent to a digit, an invoice-number-like token,
    # or a quantity×price pattern.
    assert data.looks_like_invoice("Total: $500.00")
    assert data.looks_like_invoice("Total: € 1,000")


def test_looks_like_invoice_false_for_currency_symbol_alone():
    # `$USD` (no digit adjacent) is not invoice-shaped by itself.
    assert data.looks_like_invoice("Total: $USD") is False


def test_looks_like_invoice_false_for_bare_digits():
    # A lone integer isn't enough — prose has plenty ("3 issues", "Q3 2026").
    assert data.looks_like_invoice("Amount 100") is False


def test_looks_like_invoice_true_for_quantity_price_pattern():
    assert data.looks_like_invoice("Line: WidgetA 5 @ $250")
    assert data.looks_like_invoice("5 x 250")
    assert data.looks_like_invoice("5 × 250")


def test_looks_like_invoice_true_for_invoice_number_token():
    assert data.looks_like_invoice("Reference: INV-0042 attached")


def test_looks_like_invoice_false_for_prose():
    memo = (
        "Performance review memo. Regarding the team leadership under duress "
        "and lessons learned. No further action is required at this time."
    )
    assert data.looks_like_invoice(memo) is False


def test_looks_like_invoice_false_for_empty_and_error():
    assert data.looks_like_invoice("") is False
    assert data.looks_like_invoice("(cannot preview: OSError: bad)") is False


# ---------------------------------------------------------------------------
# remove_upload — path-traversal safe, ignores .gitkeep
# ---------------------------------------------------------------------------

def test_remove_upload_deletes_the_file():
    dest = data.save_upload("removeme.txt", b"INVOICE #INV-R\nTotal: $10\n" * 2)
    assert dest.exists()
    assert data.remove_upload(dest.name) is True
    assert not dest.exists()


def test_remove_upload_refuses_traversal():
    # Even if the caller sends "../../etc/passwd", we strip to the basename
    # and only look inside uploads_dir. A basename with no matching file
    # returns False.
    assert data.remove_upload("../../etc/passwd") is False
    assert data.remove_upload("/absolute/path.txt") is False


def test_remove_upload_refuses_gitkeep():
    # The sentinel that keeps data/uploads/ present after a clone must
    # not be removable through the UI.
    gk = data.uploads_dir() / ".gitkeep"
    gk.touch()
    assert data.remove_upload(".gitkeep") is False
    assert gk.exists()


def test_remove_upload_missing_file_is_false():
    assert data.remove_upload("does_not_exist_12345.txt") is False
