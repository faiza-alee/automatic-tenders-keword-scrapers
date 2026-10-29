from datetime import date

import pytest

from app.ingest import upsert_tender
from app.nlp.pipeline import run_pending_nlp
from tests.conftest import build_client, make_settings
from tests.helpers import raw


@pytest.fixture
def seeded(session_factory, taxonomy, settings):
    with session_factory() as s:
        data = [
            raw(ref="1", title="Supply of laptops and printers", dept="Education Dept", publication_date="01-03-2026", closing_date="01-01-2099", description="Dell laptops, toner"),
            raw(ref="2", title="Development of web portal and mobile app software", dept="IT Board", publication_date="02-03-2026", closing_date="01-02-2099"),
            raw(ref="3", title="Repair of link road Gilgit", dept="C&W Dept", publication_date="03-03-2026", closing_date="01-03-2020"),
            raw(ref="4", title="Medical equipment ultrasound for hospital", dept="Health Dept", publication_date="04-03-2026", closing_date="01-01-2099", status="Cancelled"),
        ]
        for r in data:
            upsert_tender(s, r)
        s.commit()
        run_pending_nlp(s, taxonomy=taxonomy, settings=settings)


def test_auth_required_and_health_open(api, session_factory, settings):
    anon = build_client(session_factory, settings)
    assert anon.get("/api/v1/health").json()["database"] == "ok"
    r = anon.get("/api/v1/tenders")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    anon.headers["X-API-Key"] = "wrong"
    assert anon.get("/api/v1/tenders").status_code == 401
    assert api.get("/api/v1/tenders").status_code == 200


def test_no_keys_configured_rejects_everything(session_factory):
    c = build_client(session_factory, make_settings(api_keys="", admin_api_keys=""))
    c.headers["X-API-Key"] = ""
    assert c.get("/api/v1/tenders").status_code == 401


def test_list_pagination_and_envelope(api, seeded):
    r = api.get("/api/v1/tenders", params={"page_size": 3, "page": 2, "sort": "published", "order": "asc"})
    body = r.json()
    assert r.status_code == 200 and body["meta"] == {"page": 2, "page_size": 3, "total": 4, "pages": 2}
    assert [t["reference_number"] for t in body["data"]] == ["4"]
    first = api.get("/api/v1/tenders", params={"sort": "published", "order": "desc"}).json()["data"][0]
    assert first["reference_number"] == "4" and {"id", "title", "department", "keywords", "categories", "status"} <= first.keys()


def test_filters(api, seeded):
    def refs(**p):
        return sorted(t["reference_number"] for t in api.get("/api/v1/tenders", params=p).json()["data"])
    assert refs(keyword="laptops") == ["1"]
    assert refs(category="Information Technology") == ["1", "2"]                 # group filter
    assert refs(category="construction and road maintenance") == ["3"]            # case-insensitive
    assert refs(department="health") == ["4"]
    assert refs(status="open") == ["1", "2"]                                      # 3 is past closing date, 4 cancelled
    assert refs(status="closed") == ["3"] and refs(status="cancelled") == ["4"]
    assert refs(closing_before="2030-01-01") == ["3"]
    assert refs(published_after="2026-03-03") == ["3", "4"]


def test_search(api, seeded):
    r = api.get("/api/v1/tenders/search", params={"q": "web portal"}).json()
    assert [t["reference_number"] for t in r["data"]] == ["2"]
    assert [t["reference_number"] for t in api.get("/api/v1/tenders/search", params={"q": "dell"}).json()["data"]] == ["1"]  # description
    assert api.get("/api/v1/tenders/search", params={"q": "100%_"}).json()["meta"]["total"] == 0                     # wildcards escaped
    assert api.get("/api/v1/tenders/search").status_code == 422
    assert [t["reference_number"] for t in api.get("/api/v1/tenders/search", params={"q": "road", "status": "closed"}).json()["data"]] == ["3"]


def test_detail_and_keywords(api, seeded):
    tid = api.get("/api/v1/tenders", params={"keyword": "laptops"}).json()["data"][0]["id"]
    d = api.get(f"/api/v1/tenders/{tid}").json()["data"]
    assert d["description"] == "Dell laptops, toner" and d["nlp_status"] == "done" and d["nlp_method"]
    assert d["keyword_details"][0]["method"] and d["category_details"][0]["name"] == "IT Equipment and Computer Hardware"
    kws = api.get(f"/api/v1/tenders/{tid}/keywords").json()["data"]
    assert {k["term"] for k in kws} >= {"laptops", "printers"}
    assert api.get("/api/v1/tenders/9999").json()["error"]["code"] == "not_found"


def test_keywords_and_categories_lists(api, seeded):
    body = api.get("/api/v1/keywords", params={"q": "lap"}).json()
    terms = {k["term"]: k["tender_count"] for k in body["data"]}
    assert terms["laptops"] == 1 and all("lap" in t for t in terms) and body["meta"]["total"] == len(terms)
    cats = {c["name"]: c["tender_count"] for c in api.get("/api/v1/categories").json()["data"]}
    assert cats["Software and Web Development"] >= 1 and "Other" in cats


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 1000}, {"sort": "bogus"}, {"status": "weird"}, {"closing_after": "notadate"}])
def test_validation_errors_use_envelope(api, params):
    r = api.get("/api/v1/tenders", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error" and r.json()["error"]["details"]


def test_rate_limit(session_factory):
    c = build_client(session_factory, make_settings(rate_limit_per_minute=3))
    c.headers["X-API-Key"] = "test-key"
    codes = [c.get("/api/v1/tenders").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = c.get("/api/v1/tenders")
    assert r.json()["error"]["code"] == "rate_limited" and int(r.headers["Retry-After"]) >= 1
    assert c.get("/api/v1/health").status_code == 200       # health is never limited


def test_swagger_docs_available(api):
    assert api.get("/docs").status_code == 200
    spec = api.get("/openapi.json").json()
    assert "/api/v1/tenders/search" in spec["paths"] and "APIKeyHeader" in spec["components"]["securitySchemes"]


def test_admin_requires_admin_key(api, admin, seeded, monkeypatch):
    assert api.get("/api/v1/admin/status").status_code == 403
    s = admin.get("/api/v1/admin/status").json()["data"]
    assert s["total_tenders"] == 4 and s["nlp"]["done"] == 4 and s["last_run"] is None

    called = []
    monkeypatch.setattr("app.monitor.run_once", lambda trigger="manual", **kw: called.append(trigger))
    assert admin.post("/api/v1/admin/run").status_code == 202 and called == ["manual"]
    assert api.post("/api/v1/admin/run").status_code == 403


def test_admin_runs_and_reprocess(admin, session_factory, seeded):
    from app.models import ScrapingLog
    with session_factory() as s:
        s.add(ScrapingLog(trigger="scheduled", status="success", new_count=2)); s.commit()
    runs = admin.get("/api/v1/admin/runs").json()["data"]
    assert runs[0]["new"] == 2 and runs[0]["status"] == "success"
    assert admin.get("/api/v1/admin/status").json()["data"]["last_successful_run"]["id"] == runs[0]["id"]
    assert admin.post("/api/v1/admin/tenders/1/reprocess").json()["data"]["processed"] == 1
    assert admin.post("/api/v1/admin/tenders/999/reprocess").status_code == 404
    assert admin.get("/api/v1/admin/failures").json()["data"] == []


def test_dashboard_page_is_served_without_key(session_factory, settings):
    from tests.conftest import build_client
    c = build_client(session_factory, settings)
    r = c.get("/dashboard")
    assert r.status_code == 200 and "GB-PPRA Tender Monitor" in r.text and "text/html" in r.headers["content-type"]
    root = c.get("/", follow_redirects=False)
    assert root.status_code in (302, 307) and root.headers["location"] == "/dashboard"
    assert c.get("/api/v1/tenders").status_code == 401        # the data itself is still protected
