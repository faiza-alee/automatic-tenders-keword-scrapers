"""Query building for the tender endpoints."""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (Category, Department, Keyword, Tender, TenderCategory,
                        TenderKeyword)

SORTS = {
    "published": Tender.publication_date,
    "closing": Tender.closing_date,
    "first_seen": Tender.first_seen_at,
    "title": Tender.title,
}


@dataclass
class TenderFilters:
    q: str | None = None
    keyword: str | None = None
    category: str | None = None
    department: str | None = None
    status: str | None = None
    published_after: date | None = None
    published_before: date | None = None
    closing_after: date | None = None
    closing_before: date | None = None
    sort: str = "first_seen"
    order: str = "desc"
    page: int = 1
    page_size: int = 20


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _kw_exists(term_like: str, exact: bool = False):
    cond = func.lower(Keyword.term) == term_like if exact else Keyword.term.ilike(_like(term_like), escape="\\")
    return exists().where(and_(TenderKeyword.tender_id == Tender.id, TenderKeyword.keyword_id == Keyword.id, cond))


def build_query(f: TenderFilters):
    stmt = select(Tender)
    if f.q:
        for word in f.q.split()[:8]:  # every word must match somewhere
            like = _like(word)
            stmt = stmt.where(or_(
                Tender.title.ilike(like, escape="\\"),
                Tender.description.ilike(like, escape="\\"),
                Tender.reference_number.ilike(like, escape="\\"),
                Tender.department.has(Department.name.ilike(like, escape="\\")),
                _kw_exists(word),
            ))
    if f.keyword:
        stmt = stmt.where(_kw_exists(f.keyword.strip().lower(), exact=True))
    if f.category:
        name = f.category.strip().lower()
        cat_exists = exists().where(and_(
            TenderCategory.tender_id == Tender.id, TenderCategory.category_id == Category.id,
            or_(func.lower(Category.name) == name, func.lower(Category.group_name) == name)))
        stmt = stmt.where(or_(cat_exists, func.lower(Tender.source_category) == name))
    if f.department:
        stmt = stmt.where(Tender.department.has(Department.name.ilike(_like(f.department), escape="\\")))
    if f.status:
        today = date.today()
        st = f.status.lower()
        if st == "open":
            stmt = stmt.where(Tender.status.in_(("open", "unknown")), or_(Tender.closing_date.is_(None), Tender.closing_date >= today))
        elif st == "closed":
            stmt = stmt.where(or_(Tender.status == "closed",
                                  and_(Tender.status.in_(("open", "unknown")), Tender.closing_date < today)))
        else:
            stmt = stmt.where(Tender.status == st)
    if f.published_after:
        stmt = stmt.where(Tender.publication_date >= f.published_after)
    if f.published_before:
        stmt = stmt.where(Tender.publication_date <= f.published_before)
    if f.closing_after:
        stmt = stmt.where(Tender.closing_date >= f.closing_after)
    if f.closing_before:
        stmt = stmt.where(Tender.closing_date <= f.closing_before)
    return stmt


def run_query(session: Session, f: TenderFilters) -> tuple[list[Tender], int]:
    stmt = build_query(f)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col = SORTS[f.sort]
    ordered = col.desc().nulls_last() if f.order == "desc" else col.asc().nulls_last()
    stmt = (stmt.options(selectinload(Tender.department), selectinload(Tender.processing),
                         selectinload(Tender.keyword_links).selectinload(TenderKeyword.keyword),
                         selectinload(Tender.category_links).selectinload(TenderCategory.category),
                         selectinload(Tender.documents))
            .order_by(ordered, Tender.id.desc()).limit(f.page_size).offset((f.page - 1) * f.page_size))
    return list(session.scalars(stmt)), total
