from datetime import timedelta

from sqlalchemy import func, select

from app import monitor
from app.models import ScrapingLog, Tender, TenderDocument, utcnow
from app.normalize import RawTender
from app.scheduler import build_scheduler, scheduled_job
from tests.helpers import FakeSource, make_pdf, raw
from tests.conftest import make_settings


def run(session_factory, source, settings, taxonomy, trigger="scheduled"):
    return monitor.run_once(trigger, session_factory, source, settings, taxonomy=taxonomy)


def test_full_pipeline_new_tender_ends_up_with_keywords_and_docs(session_factory, settings, taxonomy):
    url = "https://x.pk/d/a.pdf"
    src = FakeSource([raw(ref="1", title="Supply of solar panels", document_urls=[url])],
                     docs={url: make_pdf("Solar panels and inverter installation, hydropower")})
    res = run(session_factory, src, settings, taxonomy)
    assert res["status"] == "success" and res["new"] == 1 and res["nlp_processed"] == 1
    with session_factory() as s:
        t = s.scalar(select(Tender))
        assert t.documents[0].status == "ok" and "inverter" in t.documents[0].text
        assert t.processing.nlp_status == "done" and t.keyword_links
        assert any(c.category.name == "Energy and Power" for c in t.category_links)


def test_second_run_detects_changes_and_no_duplicates(session_factory, settings, taxonomy):
    run(session_factory, FakeSource([raw(ref="1"), raw(ref="2", title="Road repair")]), settings, taxonomy)
    res = run(session_factory, FakeSource([raw(ref="1"), raw(ref="2", title="Road repair", closing_date="05-05-2099"),
                                          raw(ref="3", title="New thing")]), settings, taxonomy)
    assert (res["new"], res["updated"], res["unchanged"]) == (1, 1, 1)
    with session_factory() as s:
        assert s.scalar(select(func.count(Tender.id))) == 3
        assert s.scalar(select(ScrapingLog).order_by(ScrapingLog.id.desc())).updated_count == 1


def test_one_bad_tender_does_not_stop_others(session_factory, settings, taxonomy):
    src = FakeSource([raw(ref="1"), RawTender(title=None, reference_number="bad"), raw(ref="3", title="Another")])
    res = run(session_factory, src, settings, taxonomy)
    assert res["status"] == "partial" and res["new"] == 2 and res["failed"] == 1
    with session_factory() as s:
        log = s.scalar(select(ScrapingLog))
        assert log.details["failed_records"][0]["reference"] == "bad"
        assert s.scalar(select(func.count(Tender.id))) == 2


def test_document_failure_is_recorded_and_tender_still_saved(session_factory, settings, taxonomy):
    res = run(session_factory, FakeSource([raw(document_urls=["https://x.pk/missing.pdf"])]), settings, taxonomy)
    assert res["status"] == "success"
    with session_factory() as s:
        d = s.scalar(select(TenderDocument))
        assert d.status == "failed" and d.attempts == 1 and "404" in d.error
    run(session_factory, FakeSource([raw(document_urls=["https://x.pk/missing.pdf"])]), settings, taxonomy)   # retried later
    with session_factory() as s:
        assert s.scalar(select(TenderDocument)).attempts == 2


def test_listing_failure_keeps_partial_progress_and_next_run_works(session_factory, settings, taxonomy):
    res = run(session_factory, FakeSource([raw(ref="1"), raw(ref="2")], fail_after=1), settings, taxonomy)
    assert res["status"] == "partial" and res["new"] == 1 and "timed out" in res["error"]
    res = run(session_factory, FakeSource([raw(ref="1"), raw(ref="2")]), settings, taxonomy)   # next run unaffected
    assert res["status"] == "success" and res["new"] == 1 and res["unchanged"] == 1


def test_total_failure_logged_as_failed_then_recovers(session_factory, settings, taxonomy):
    res = run(session_factory, FakeSource([raw()], fail_after=0), settings, taxonomy)
    assert res["status"] == "failed"
    assert run(session_factory, FakeSource([raw()]), settings, taxonomy)["status"] == "success"


def test_concurrent_run_is_skipped_but_stale_run_is_abandoned(session_factory, settings, taxonomy):
    with session_factory() as s:
        s.add(ScrapingLog(trigger="manual", status="running")); s.commit()
    assert run(session_factory, FakeSource([raw()]), settings, taxonomy)["status"] == "skipped"
    with session_factory() as s:
        s.scalar(select(ScrapingLog)).started_at = utcnow() - timedelta(hours=5); s.commit()
    assert run(session_factory, FakeSource([raw()]), settings, taxonomy)["status"] == "success"
    with session_factory() as s:
        assert {l.status for l in s.scalars(select(ScrapingLog))} == {"abandoned", "success"}


def test_manual_and_scheduled_triggers_recorded(session_factory, settings, taxonomy):
    run(session_factory, FakeSource([raw()]), settings, taxonomy, trigger="manual")
    run(session_factory, FakeSource([raw()]), settings, taxonomy, trigger="scheduled")
    with session_factory() as s:
        assert [l.trigger for l in s.scalars(select(ScrapingLog).order_by(ScrapingLog.id))] == ["manual", "scheduled"]


def test_scheduler_interval_from_settings_and_job_never_raises():
    sched = build_scheduler(make_settings(scrape_interval_minutes=30), job=lambda: None)
    job = sched.get_job("scrape") if sched.state else sched._pending_jobs[0][0]
    assert job.trigger.interval == timedelta(minutes=30) and job.max_instances == 1 and job.coalesce

    calls = []
    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
    scheduled_job(flaky); scheduled_job(flaky)     # first raises internally, second still runs
    assert len(calls) == 2
