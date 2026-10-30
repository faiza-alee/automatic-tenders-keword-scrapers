"""Runs keyword extraction + category classification and stores results separately from the original text."""
import logging
import re

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (Category, Keyword, ProcessingStatus, Tender, TenderCategory,
                        TenderKeyword, utcnow)
from app.nlp.extractors import BOILERPLATE, BaseExtractor, ExtractedKeyword, get_extractor, snippet_for
from app.nlp.taxonomy import Taxonomy

log = logging.getLogger(__name__)
MAX_TEXT_CHARS = 60_000


def sync_taxonomy(session: Session, taxonomy: Taxonomy) -> None:
    """Make the categories table mirror the taxonomy file (adds new, updates groups; never deletes)."""
    existing = {c.name: c for c in session.scalars(select(Category))}
    for name, group in taxonomy.all_categories():
        cat = existing.get(name)
        if cat is None:
            session.add(Category(name=name, group_name=group, taxonomy_version=taxonomy.version))
        else:
            cat.group_name, cat.taxonomy_version = group, taxonomy.version
    session.flush()


_NOISE = re.compile(r"(public\s+procurement\s+regulatory\s+authority\s*(\(\s*ppra\s*\))?|scanned\s+with\s+camscanner|camscanner)", re.I)


def build_text(tender: Tender) -> tuple[str, str]:
    """(title, body). The body contains description, eligibility and document text."""
    body = [_NOISE.sub(" ", tender.description or ""), _NOISE.sub(" ", tender.eligibility or "")]
    body += [_NOISE.sub(" ", d.text) for d in tender.documents if d.text]   # page header/scanner stamps on every PDF
    return tender.title or "", "\n".join(b for b in body if b)[:MAX_TEXT_CHARS]


def _keyword(session: Session, term: str) -> Keyword:
    kw = session.scalar(select(Keyword).where(Keyword.term == term))
    if kw is None:
        kw = Keyword(term=term)
        session.add(kw)
        session.flush()
    return kw


def process_tender(session: Session, tender: Tender, extractor: BaseExtractor, taxonomy: Taxonomy,
                   max_keywords: int = 15) -> None:
    title, body = build_text(tender)
    full = f"{title}. {body}".strip()
    method = f"{extractor.version}+taxonomy-{taxonomy.version}"

    dept_tokens = set(re.findall(r"[a-z0-9]+", (tender.department.name if tender.department else "").lower()))

    def is_office_phrase(term: str) -> bool:
        """Multi-word phrases made only of the issuing office's own words ('deputy director education') describe
        who is buying, not what is bought; every PDF repeats them in its letterhead."""
        toks = re.findall(r"[a-z0-9]+", term)
        return len(toks) >= 2 and all(t in dept_tokens or t in BOILERPLATE for t in toks)

    keywords = {k.term: k for k in extractor.extract(full, max_keywords + 10) if not is_office_phrase(k.term)}
    for k in extractor.extract(title, max_keywords):  # the title says what is being bought: its phrases rank first
        if is_office_phrase(k.term):
            continue
        old = keywords.get(k.term)
        keywords[k.term] = ExtractedKeyword(k.term, min(1.0, 0.9 + 0.1 * k.score), (old.snippet if old else None) or k.snippet)
    matches = taxonomy.classify(title, body)
    for m in matches:  # taxonomy terms found in the text are strong, human-curated keywords
        for term in m.matched_terms:
            toks = re.findall(r"[a-z0-9\-/]+", term)
            if len(term) <= 2 or all(t in BOILERPLATE for t in toks):
                continue  # abbreviations like 'const' or 'mw' help classification but are useless as keywords
            if term not in keywords:
                keywords[term] = ExtractedKeyword(term, min(1.0, 0.6 + m.score * 0.4), snippet_for(term, full))

    tender.keyword_links.clear()
    tender.category_links.clear()
    session.flush()
    for term, k in sorted(keywords.items(), key=lambda kv: -kv[1].score)[: max_keywords + 10]:
        tender.keyword_links.append(TenderKeyword(keyword=_keyword(session, term[:255]), score=round(float(k.score), 4),
                                                  method=method, snippet=k.snippet))
    cats = {c.name: c for c in session.scalars(select(Category))}
    for m in matches:
        cat = cats.get(m.name)
        if cat is None:
            cat = Category(name=m.name, group_name=m.group, taxonomy_version=taxonomy.version)
            session.add(cat)
            session.flush()
        tender.category_links.append(TenderCategory(category=cat, score=round(float(m.score), 4), method=method,
                                                    matched_terms=m.matched_terms))
    ps = tender.processing or ProcessingStatus(tender=tender)
    ps.nlp_status, ps.nlp_content_hash, ps.nlp_method = "done", tender.content_hash, method
    ps.last_error, ps.processed_at = None, utcnow()
    session.flush()


def mark_for_reprocess(session: Session, tender_id: int | None = None) -> int:
    q = select(ProcessingStatus)
    if tender_id is not None:
        q = q.where(ProcessingStatus.tender_id == tender_id)
    rows = list(session.scalars(q))
    for ps in rows:
        ps.nlp_status, ps.attempts = "pending", 0
    session.flush()
    return len(rows)


def pending_query(max_attempts: int):
    return (select(Tender).outerjoin(ProcessingStatus, ProcessingStatus.tender_id == Tender.id)
            .where(or_(ProcessingStatus.tender_id.is_(None),
                       ProcessingStatus.nlp_status == "pending",
                       ProcessingStatus.nlp_content_hash.is_(None),
                       ProcessingStatus.nlp_content_hash != Tender.content_hash,
                       and_(ProcessingStatus.nlp_status == "failed", ProcessingStatus.attempts < max_attempts)))
            .order_by(Tender.id))


def run_pending_nlp(session: Session, extractor: BaseExtractor | None = None, taxonomy: Taxonomy | None = None,
                    settings: Settings | None = None, limit: int | None = None) -> dict:
    """Process every tender that is new, changed, flagged for reprocessing, or failed with attempts left.
    One bad tender never stops the others; failures are recorded on processing_status."""
    settings = settings or get_settings()
    extractor = extractor or get_extractor(settings.keyword_method)
    taxonomy = taxonomy or Taxonomy.from_yaml(settings.taxonomy_path)
    sync_taxonomy(session, taxonomy)
    session.commit()
    q = pending_query(settings.max_nlp_attempts)
    if limit:
        q = q.limit(limit)
    done = failed = 0
    for tender in list(session.scalars(q)):
        tid = tender.id
        try:
            with session.begin_nested():
                ps = tender.processing
                if ps is not None and ps.nlp_content_hash != tender.content_hash:
                    ps.attempts = 0  # content changed: fresh start
                process_tender(session, tender, extractor, taxonomy, settings.max_keywords)
            done += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.exception("nlp failed", extra={"ctx": {"tender_id": tid}})
            tender = session.get(Tender, tid)
            ps = tender.processing or ProcessingStatus(tender=tender)
            ps.nlp_status, ps.attempts = "failed", (ps.attempts or 0) + 1
            ps.nlp_content_hash, ps.last_error = tender.content_hash, f"{type(e).__name__}: {e}"[:2000]
        session.commit()
    return {"processed": done, "failed": failed}
