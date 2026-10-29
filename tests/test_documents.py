from app.scraper.documents import extract_text
from tests.helpers import make_pdf


def test_pdf_text():
    assert "solar panels" in extract_text(make_pdf("Supply of solar panels for Gilgit"), "application/pdf", "a.pdf")


def test_unsupported_and_empty():
    assert extract_text(b"\xd0\xcf\x11\xe0 legacy doc", "application/msword", "a.doc") is None
    assert extract_text(make_pdf(""), "application/pdf", "blank.pdf") == ""


def test_plain_text():
    assert extract_text(b"hello", "text/plain", "a.txt") == "hello"
