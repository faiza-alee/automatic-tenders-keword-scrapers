"""One monitoring cycle: scrape -> detect new/changed -> store -> fetch docs -> keywords -> log."""
import logging
import threading
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.ingest import compute_content_hash, upsert_tender
from app.models import ScrapingLog, Tender, utcnow
from app.nlp.pipeline import run_pending_nlp
from app.normalize import ValidationError, sha256
from app.scraper.documents import extract_text
from app.scraper.source import TenderSource

log = logging.getLogger(__name__)
_in_process_lock = threading.Lock()


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=utcnow().tzinfo)


def sync_documents(session: Session, tender: Tender, source, settings: Settings) -> int:
    """Download + extract text for documents that have none yet. Failures are recorded, never raised."""
    if not settings.download_documents:
        return 0
    got = 0
    for doc in tender.documents:
        if doc.status in ("ok", "no_text", "skipped") or doc.attempts >= settings.max_document_attempts:
            continue
        doc.attempts += 1
        try:
            content, ctype = source.download(doc.url)
            text = extract_text(content, ctype, doc.filename or "", ocr=settings.ocr_enabled)
            doc.content_type, doc.downloaded_at, doc.error = ctype or None, utcnow(), None
            if text is None:
                doc.status = "skipped"  # unsupported format (e.g. legacy .doc)
            elif not text.strip():
                doc.status = "no_text"  # scanned PDF without OCR
            else:
                doc.status, doc.text, doc.text_hash = "ok", text, sha256(text)
                got += 1
        except Exception as e:  # noqa: BLE001
            doc.status, doc.error = "failed", f"{type(e).__name__}: {e}"[:1000]
            log.warning("document failed", extra={"ctx": {"url": doc.url, "error": str(e)}})
    tender.content_hash = compute_content_hash(tender)
    return got


def _begin_run(session: Session, trigger: str, settings: Settings) -> ScrapingLog | None:
    """DB-level guard so the API process, the worker and the CLI never run two cycles at once."""
    cutoff = utcnow() - timedelta(minutes=settings.run_timeout_minutes)
    for stale in session.scalars(select(ScrapingLog).where(ScrapingLog.status == "running")):
        if _aware(stale.started_at) < cutoff:
            stale.status, stale.finished_at = "abandoned", utcnow()
            stale.error = "run exceeded timeout / process died"
        else:
            session.rollback()
            return None
    run = ScrapingLog(trigger=trigger, status="running")
    session.add(run)
    session.commit()
    return run


def run_once(trigger: str = "scheduled", session_factory: sessionmaker | None = None, source=None,
             settings: Settings | None = None, extractor=None, taxonomy=None) -> dict:
    settings = settings or get_settings()
    session_factory = session_factory or get_session_factory()
    if not _in_process_lock.acquire(blocking=False):
        return {"status": "skipped", "reason": "a run is already in progress"}
    session = session_factory()
    run = None
    try:
        run = _begin_run(session, trigger, settings)
        if run is None:
            return {"status": "skipped", "reason": "a run is already in progress"}
        failed_records: list[dict] = []
        run_error = None
        try:
            source = source or TenderSource(settings)
            for raw in source.iter_tenders():
                run.found_count += 1
                try:
                    with session.begin_nested():
                        result = upsert_tender(session, raw)
                        sync_documents(session, result.tender, source, settings)
                    setattr(run, f"{result.outcome}_count", getattr(run, f"{result.outcome}_count") + 1)
                except Exception as e:  # noqa: BLE001 - one bad tender must not stop the rest
                    run.failed_count += 1
                    kind = "invalid" if isinstance(e, ValidationError) else type(e).__name__
                    failed_records.append({"reference": raw.reference_number, "title": (raw.title or "")[:120],
                                           "url": raw.source_url, "error": f"{kind}: {e}"[:300]})
                    log.warning("tender failed", extra={"ctx": failed_records[-1]})
                session.commit()
        except Exception as e:  # noqa: BLE001 - listing/pagination failure: keep what we have
            run_error = f"{type(e).__name__}: {e}"[:2000]
            log.error("scrape aborted", exc_info=True)
            session.rollback()
        run.pages_fetched = getattr(source, "pages_fetched", 0)
        try:
            nlp = run_pending_nlp(session, extractor, taxonomy, settings)
            run.nlp_processed, run.nlp_failed = nlp["processed"], nlp["failed"]
        except Exception as e:  # noqa: BLE001
            run_error = (run_error + " | " if run_error else "") + f"nlp: {type(e).__name__}: {e}"[:500]
            log.error("nlp stage failed", exc_info=True)
            session.rollback()
        run.error, run.details = run_error, {"failed_records": failed_records}
        productive = run.new_count + run.updated_count + run.unchanged_count > 0
        if run_error and not productive:
            run.status = "failed"
        elif run_error or run.failed_count or run.nlp_failed:
            run.status = "partial"
        else:
            run.status = "success"
        run.finished_at = utcnow()
        session.commit()
        summary = {"status": run.status, "run_id": run.id, "found": run.found_count, "new": run.new_count,
                   "updated": run.updated_count, "unchanged": run.unchanged_count, "failed": run.failed_count,
                   "nlp_processed": run.nlp_processed, "error": run.error}
        log.info("run finished", extra={"ctx": summary})
        return summary
    except Exception as e:  # noqa: BLE001 - last line of defence: the next scheduled run must still work
        log.exception("run crashed")
        session.rollback()
        if run is not None:
            run = session.get(ScrapingLog, run.id)
            run.status, run.error, run.finished_at = "failed", f"{type(e).__name__}: {e}"[:2000], utcnow()
            session.commit()
        return {"status": "failed", "error": str(e)}
    finally:
        session.close()
        _in_process_lock.release()
