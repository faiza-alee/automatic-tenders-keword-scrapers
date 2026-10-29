"""Duplicate-safe insert/update of tenders with change detection."""
import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (Department, ProcessingStatus, Tender, TenderChange,
                        TenderDocument, utcnow)
from app.normalize import (RawTender, filename_from_url, make_dedupe_key,
                           normalize_raw, sha256)

log = logging.getLogger(__name__)

# normalized attribute -> Tender column
TRACKED = {
    "title": "title",
    "description": "description",
    "closing_date": "closing_date",
    "publication_date": "publication_date",
    "status": "status",
    "eligibility": "eligibility",
    "category": "source_category",
}


@dataclass
class UpsertResult:
    tender: Tender
    outcome: str  # new | updated | unchanged
    changes: list[str] = field(default_factory=list)


def compute_content_hash(tender: Tender) -> str:
    """Hash of everything the NLP step reads. If it changes, keywords are recomputed."""
    parts = [tender.title or "", tender.description or "", tender.eligibility or ""]
    parts += sorted(d.text_hash for d in tender.documents if d.text_hash)
    return sha256("\n".join(parts))


def _department(session: Session, name: str | None) -> Department | None:
    if not name:
        return None
    dept = session.scalar(select(Department).where(Department.name == name))
    if dept is None:
        dept = Department(name=name)
        session.add(dept)
        session.flush()
    return dept


def upsert_tender(session: Session, raw: RawTender, now: datetime | None = None) -> UpsertResult:
    n = normalize_raw(raw)
    now = now or utcnow()
    key = make_dedupe_key(n.reference_number, n.department, n.title)

    tender = session.scalar(select(Tender).where(Tender.dedupe_key == key))
    if tender is None and not n.reference_number and n.source_url:
        tender = session.scalar(select(Tender).where(Tender.source_url == n.source_url, Tender.reference_number.is_(None)))

    dept = _department(session, n.department)

    if tender is None:
        tender = Tender(
            dedupe_key=key, reference_number=n.reference_number, title=n.title, description=n.description,
            department=dept, source_category=n.category, publication_date=n.publication_date,
            closing_date=n.closing_date, status=n.status, eligibility=n.eligibility,
            source_url=n.source_url, raw_data=raw.as_dict(), first_seen_at=now, last_seen_at=now,
        )
        for url in n.document_urls:
            tender.documents.append(TenderDocument(url=url, filename=filename_from_url(url)))
        tender.processing = ProcessingStatus(nlp_status="pending")
        session.add(tender)
        tender.content_hash = compute_content_hash(tender)
        session.flush()
        return UpsertResult(tender, "new")

    changes: list[str] = []

    def record(fieldname, old, new):
        changes.append(fieldname)
        session.add(TenderChange(tender=tender, field=fieldname, old_value=None if old is None else str(old),
                                 new_value=None if new is None else str(new), changed_at=now))

    for attr, column in TRACKED.items():
        new, old = getattr(n, attr), getattr(tender, column)
        if new is None or new == old:  # never wipe stored data because a page was sparse
            continue
        record(column, old, new)
        setattr(tender, column, new)
    if dept is not None and tender.department_id != dept.id:
        record("department", tender.department.name if tender.department else None, dept.name)
        tender.department = dept
    if n.source_url and not tender.source_url:
        tender.source_url = n.source_url

    known = {d.url for d in tender.documents}
    added = [u for u in n.document_urls if u not in known]
    for url in added:
        tender.documents.append(TenderDocument(url=url, filename=filename_from_url(url)))
    if added:
        record("document_links", None, ", ".join(added))

    tender.last_seen_at = now
    if tender.processing is None:
        tender.processing = ProcessingStatus(nlp_status="pending")
    if changes:
        tender.raw_data = raw.as_dict()
        tender.content_hash = compute_content_hash(tender)
        session.flush()
        return UpsertResult(tender, "updated", changes)
    session.flush()
    return UpsertResult(tender, "unchanged")
