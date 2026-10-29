import math
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app.api import schemas as S
from app.api.queries import SORTS, TenderFilters, run_query
from app.api.security import require_api_key
from app.db import get_db
from app.models import Category, Keyword, Tender, TenderCategory, TenderKeyword

public = APIRouter(prefix="/api/v1")
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])

ERRORS = {401: {"model": S.ErrorResponse}, 422: {"model": S.ErrorResponse}, 429: {"model": S.ErrorResponse}}


def _meta(page, size, total):
    return S.Meta(page=page, page_size=size, total=total, pages=math.ceil(total / size) if total else 0)


def _summary(t: Tender) -> dict:
    kws = sorted(t.keyword_links, key=lambda k: -(k.score or 0))
    cats = sorted(t.category_links, key=lambda c: -(c.score or 0))
    return dict(
        id=t.id, reference_number=t.reference_number, title=t.title,
        department=t.department.name if t.department else None, source_category=t.source_category,
        categories=[c.category.name for c in cats], keywords=[k.keyword.term for k in kws[:10]],
        publication_date=t.publication_date, closing_date=t.closing_date, status=t.effective_status,
        source_url=t.source_url, first_seen_at=t.first_seen_at, updated_at=t.updated_at,
    )


def _detail(t: Tender) -> S.TenderDetail:
    kws = sorted(t.keyword_links, key=lambda k: -(k.score or 0))
    cats = sorted(t.category_links, key=lambda c: -(c.score or 0))
    base = _summary(t)
    base["keywords"] = [k.keyword.term for k in kws]
    return S.TenderDetail(
        **base, description=t.description, eligibility=t.eligibility, last_seen_at=t.last_seen_at,
        documents=[S.DocumentOut(url=d.url, filename=d.filename, status=d.status, has_text=bool(d.text)) for d in t.documents],
        keyword_details=[S.KeywordOut(term=k.keyword.term, score=k.score, method=k.method, snippet=k.snippet) for k in kws],
        category_details=[S.CategoryOut(name=c.category.name, group=c.category.group_name, score=c.score,
                                        matched_terms=c.matched_terms) for c in cats],
        nlp_status=t.processing.nlp_status if t.processing else None,
        nlp_method=t.processing.nlp_method if t.processing else None,
    )


def filters(
    q: str | None = Query(None, max_length=200, description="Free-text search (all words must match)"),
    keyword: str | None = Query(None, max_length=100, description="Exact keyword, e.g. 'software'"),
    category: str | None = Query(None, max_length=200, description="Category or category group, e.g. 'Information Technology'"),
    department: str | None = Query(None, max_length=200),
    status: Literal["open", "closed", "cancelled", "awarded", "unknown"] | None = None,
    published_after: date | None = None, published_before: date | None = None,
    closing_after: date | None = None, closing_before: date | None = None,
    sort: Literal["published", "closing", "first_seen", "title"] = "first_seen",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100),
) -> TenderFilters:
    return TenderFilters(q=q, keyword=keyword, category=category, department=department, status=status,
                         published_after=published_after, published_before=published_before,
                         closing_after=closing_after, closing_before=closing_before,
                         sort=sort, order=order, page=page, page_size=page_size)


@public.get("/health", response_model=S.Health, tags=["System"])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        dbstatus = "ok"
    except Exception:  # noqa: BLE001
        dbstatus = "error"
    return S.Health(status="ok" if dbstatus == "ok" else "degraded", database=dbstatus, time=datetime.now(timezone.utc))


@router.get("/tenders", response_model=S.ListResponse[S.TenderSummary], responses=ERRORS, tags=["Tenders"],
            summary="List tenders (paginated, filterable, sortable)")
def list_tenders(f: TenderFilters = Depends(filters), db: Session = Depends(get_db)):
    items, total = run_query(db, f)
    return S.ListResponse(data=[S.TenderSummary(**_summary(t)) for t in items], meta=_meta(f.page, f.page_size, total))


@router.get("/tenders/search", response_model=S.ListResponse[S.TenderSummary], responses=ERRORS, tags=["Tenders"],
            summary="Search tenders by text")
def search_tenders(q: str = Query(..., min_length=2, max_length=200), f: TenderFilters = Depends(filters),
                   db: Session = Depends(get_db)):
    f.q = q
    items, total = run_query(db, f)
    return S.ListResponse(data=[S.TenderSummary(**_summary(t)) for t in items], meta=_meta(f.page, f.page_size, total))


def _get_tender(db: Session, tender_id: int) -> Tender:
    t = db.scalar(select(Tender).where(Tender.id == tender_id).options(
        selectinload(Tender.department), selectinload(Tender.documents), selectinload(Tender.processing),
        selectinload(Tender.keyword_links).selectinload(TenderKeyword.keyword),
        selectinload(Tender.category_links).selectinload(TenderCategory.category)))
    if t is None:
        raise HTTPException(404, f"Tender {tender_id} not found")
    return t


@router.get("/tenders/{tender_id}", response_model=S.ItemResponse[S.TenderDetail], responses=ERRORS, tags=["Tenders"])
def get_tender(tender_id: int, db: Session = Depends(get_db)):
    return S.ItemResponse(data=_detail(_get_tender(db, tender_id)))


@router.get("/tenders/{tender_id}/keywords", response_model=S.ItemResponse[list[S.KeywordOut]], responses=ERRORS, tags=["Keywords"])
def tender_keywords(tender_id: int, db: Session = Depends(get_db)):
    d = _detail(_get_tender(db, tender_id))
    return S.ItemResponse(data=d.keyword_details)


@router.get("/keywords", response_model=S.ListResponse[S.KeywordCount], responses=ERRORS, tags=["Keywords"],
            summary="Available keywords with the number of tenders using each")
def list_keywords(q: str | None = Query(None, max_length=100), page: int = Query(1, ge=1),
                  page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    cnt = func.count(TenderKeyword.tender_id)
    stmt = select(Keyword.term, cnt.label("n")).join(TenderKeyword, TenderKeyword.keyword_id == Keyword.id).group_by(Keyword.id)
    if q:
        stmt = stmt.where(Keyword.term.ilike("%" + q.replace("%", "").replace("_", "") + "%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(cnt.desc(), Keyword.term).limit(page_size).offset((page - 1) * page_size)).all()
    return S.ListResponse(data=[S.KeywordCount(term=r.term, tender_count=r.n) for r in rows], meta=_meta(page, page_size, total))


@router.get("/categories", response_model=S.ItemResponse[list[dict]], responses=ERRORS, tags=["Keywords"],
            summary="Categories with tender counts")
def list_categories(db: Session = Depends(get_db)):
    rows = db.execute(select(Category.name, Category.group_name, func.count(TenderCategory.tender_id))
                      .outerjoin(TenderCategory, TenderCategory.category_id == Category.id)
                      .group_by(Category.id).order_by(Category.name)).all()
    return S.ItemResponse(data=[{"name": n, "group": g, "tender_count": c} for n, g, c in rows])
