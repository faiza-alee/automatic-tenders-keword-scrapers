from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import monitor
from app.api import schemas as S
from app.api.security import require_admin
from app.config import get_settings
from app.db import get_db
from app.models import ProcessingStatus, ScrapingLog, Tender
from app.nlp.pipeline import mark_for_reprocess, run_pending_nlp

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(require_admin)], tags=["Admin"])


def _run(r: ScrapingLog) -> dict:
    return {"id": r.id, "trigger": r.trigger, "status": r.status, "started_at": r.started_at, "finished_at": r.finished_at,
            "pages_fetched": r.pages_fetched, "found": r.found_count, "new": r.new_count, "updated": r.updated_count,
            "unchanged": r.unchanged_count, "failed": r.failed_count, "nlp_processed": r.nlp_processed,
            "nlp_failed": r.nlp_failed, "error": r.error, "failed_records": (r.details or {}).get("failed_records", [])}


@router.get("/status", summary="Monitoring and processing status")
def status(db: Session = Depends(get_db)):
    s = get_settings()
    last = db.scalar(select(ScrapingLog).order_by(ScrapingLog.started_at.desc()).limit(1))
    last_ok = db.scalar(select(ScrapingLog).where(ScrapingLog.status.in_(("success", "partial")))
                        .order_by(ScrapingLog.started_at.desc()).limit(1))
    nlp = dict(db.execute(select(ProcessingStatus.nlp_status, func.count()).group_by(ProcessingStatus.nlp_status)).all())
    return {"data": {
        "scrape_interval_minutes": s.scrape_interval_minutes,
        "total_tenders": db.scalar(select(func.count(Tender.id))),
        "last_run": _run(last) if last else None,
        "last_successful_run": _run(last_ok) if last_ok else None,
        "nlp": {"pending": nlp.get("pending", 0), "done": nlp.get("done", 0), "failed": nlp.get("failed", 0)},
    }}


@router.get("/runs", summary="Recent scraping runs")
def runs(limit: int = Query(20, ge=1, le=200), db: Session = Depends(get_db)):
    rows = db.scalars(select(ScrapingLog).order_by(ScrapingLog.started_at.desc()).limit(limit))
    return {"data": [_run(r) for r in rows]}


@router.post("/run", status_code=202, summary="Trigger a scraping run now (asynchronous)")
def trigger_run(background: BackgroundTasks):
    background.add_task(monitor.run_once, "manual")
    return {"data": {"accepted": True, "note": "Run started in background; see /admin/runs"}}


@router.post("/tenders/{tender_id}/reprocess", summary="Re-run keyword extraction for one tender")
def reprocess(tender_id: int, db: Session = Depends(get_db)):
    if db.get(Tender, tender_id) is None:
        raise HTTPException(404, f"Tender {tender_id} not found")
    mark_for_reprocess(db, tender_id)
    db.commit()
    return {"data": run_pending_nlp(db, settings=get_settings())}


@router.get("/failures", summary="Tenders whose keyword extraction failed")
def failures(db: Session = Depends(get_db)):
    rows = db.execute(select(ProcessingStatus.tender_id, ProcessingStatus.attempts, ProcessingStatus.last_error)
                      .where(ProcessingStatus.nlp_status == "failed")).all()
    return {"data": [{"tender_id": r[0], "attempts": r[1], "error": r[2]} for r in rows]}
