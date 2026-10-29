from datetime import date

import pytest

from app.normalize import (RawTender, ValidationError, make_dedupe_key, normalize_raw,
                           normalize_status, parse_date)


@pytest.mark.parametrize("text,expected", [
    ("16-08-2026", date(2026, 8, 16)), ("16 Aug 2026", date(2026, 8, 16)), ("2026-08-16", date(2026, 8, 16)),
    ("1st September, 2026", date(2026, 9, 1)), ("16/08/2026 11:30 am", date(2026, 8, 16)),
    ("", None), (None, None), ("not a date", None), ("5", None),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_dayfirst():
    assert parse_date("03/04/2026") == date(2026, 4, 3)


def test_status_rules():
    today = date(2026, 6, 1)
    assert normalize_status("Open", date(2026, 7, 1), today) == "open"
    assert normalize_status("Open", date(2026, 5, 1), today) == "closed"
    assert normalize_status("Cancelled", None, today) == "cancelled"
    assert normalize_status(None, date(2026, 7, 1), today) == "open"
    assert normalize_status(None, None, today) == "unknown"


def test_dedupe_key_stable_and_scoped_by_department():
    a = make_dedupe_key("AB/12-2026", "GB Health Dept", "x")
    assert a == make_dedupe_key(" ab 12 2026 ", "gb health  dept", "y")      # formatting differences ignored
    assert a != make_dedupe_key("AB/12-2026", "GB Education", "x")           # same ref, other agency
    assert make_dedupe_key(None, "d", "Supply of paper") == make_dedupe_key(None, "d", "supply of  PAPER")


def test_normalize_requires_title():
    with pytest.raises(ValidationError):
        normalize_raw(RawTender(title="  ", reference_number="1"))


def test_normalize_cleans_and_dedupes_docs():
    n = normalize_raw(RawTender(title="  Supply   of\xa0paper ", closing_date="01-01-2099",
                                document_urls=["http://a/x.pdf", "http://a/x.pdf", ""]))
    assert n.title == "Supply of paper" and n.document_urls == ["http://a/x.pdf"] and n.status == "open"
