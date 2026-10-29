from sqlalchemy import func, select

from app.ingest import upsert_tender
from app.models import Tender, TenderChange
from app.normalize import ValidationError, RawTender
import pytest
from tests.helpers import raw


def test_new_then_unchanged_no_duplicates(session):
    r1 = upsert_tender(session, raw())
    r2 = upsert_tender(session, raw())
    session.commit()
    assert (r1.outcome, r2.outcome) == ("new", "unchanged")
    assert session.scalar(select(func.count(Tender.id))) == 1
    t = r1.tender
    assert t.processing.nlp_status == "pending" and t.department.name == "Education Dept" and t.raw_data["title"]


def test_same_reference_different_department_is_distinct(session):
    upsert_tender(session, raw(dept="A")); upsert_tender(session, raw(dept="B"))
    session.commit()
    assert session.scalar(select(func.count(Tender.id))) == 2


def test_reference_formatting_differences_still_dedupe(session):
    upsert_tender(session, raw(ref="AB/12-2026")); r = upsert_tender(session, raw(ref="ab 12 2026"))
    assert r.outcome == "unchanged"


def test_detects_changed_closing_date_description_status_and_docs(session):
    upsert_tender(session, raw(description="old", document_urls=["http://x/a.pdf"]))
    session.commit()
    r = upsert_tender(session, raw(description="new text", closing_date="02-02-2099", status="Cancelled",
                                   document_urls=["http://x/a.pdf", "http://x/b.pdf"]))
    session.commit()
    assert r.outcome == "updated"
    assert set(r.changes) == {"description", "closing_date", "status", "document_links"}
    fields = {c.field for c in session.scalars(select(TenderChange))}
    assert fields == set(r.changes)
    assert len(r.tender.documents) == 2 and r.tender.status == "cancelled"


def test_sparse_page_does_not_wipe_data(session):
    upsert_tender(session, raw(description="full description", eligibility="PEC"))
    r = upsert_tender(session, raw(description=None, eligibility=None))
    assert r.outcome == "unchanged" and r.tender.description == "full description"


def test_content_change_alters_hash(session):
    r1 = upsert_tender(session, raw(description="a")); h1 = r1.tender.content_hash
    r2 = upsert_tender(session, raw(description="b"))
    assert r2.tender.content_hash != h1


def test_invalid_tender_rejected(session):
    with pytest.raises(ValidationError):
        upsert_tender(session, RawTender(title=None))


def test_no_reference_falls_back_to_title_and_department(session):
    a = upsert_tender(session, raw(ref=None, title="Supply of paper"))
    b = upsert_tender(session, raw(ref=None, title="supply of  paper"))
    assert a.tender.id == b.tender.id
