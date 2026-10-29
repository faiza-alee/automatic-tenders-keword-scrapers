from app.scraper.source import TenderSource
from tests.conftest import DATA, make_settings


class FakeClient:
    def __init__(self, pages):
        self.pages, self.requested = pages, []

    def get_html(self, url):
        self.requested.append(url)
        if url not in self.pages:
            from app.scraper.client import FetchError
            raise FetchError("404")
        return self.pages[url]

    def download(self, url):
        return b"x", "text/plain"


def page(name):
    return (DATA / name).read_text(encoding="utf-8")


CFG = {"base_url": "https://example.gov.pk", "listing_urls": ["/tenders"], "listing": {"mode": "auto"},
       "detail": {"enabled": True, "mode": "auto"}}


def test_pagination_and_detail_enrichment():
    client = FakeClient({
        "https://example.gov.pk/tenders": page("listing_page1.html"),
        "https://example.gov.pk/tenders?page=2": page("listing_page2.html"),
        "https://example.gov.pk/tender/gbh-1-5": page("detail.html"),
    })
    src = TenderSource(make_settings(), client, CFG)
    rows = list(src.iter_tenders())
    assert [r.reference_number for r in rows] == ["AB/Cash/Notice/522", "GBH-1(5)/2026", "IT-77", "HW-9"]
    enriched = rows[1]
    assert enriched.description.startswith("Metaling") and len(enriched.document_urls) == 2
    assert enriched.title.startswith("Supply of Stationery")          # listing identity kept
    assert "https://example.gov.pk/doc/tender-522.pdf" not in client.requested   # documents are not fetched as pages
    assert src.pages_fetched == 3     # 2 listing pages + 1 detail page


def test_missing_detail_page_falls_back_to_listing_data():
    client = FakeClient({"https://example.gov.pk/tenders": page("listing_page2.html")})
    rows = list(TenderSource(make_settings(), client, CFG).iter_tenders())
    assert len(rows) == 1


def test_pagination_loop_and_max_pages_guard():
    loop = '<table><tr><th>Title</th><th>Ref</th></tr><tr><td>Something to buy</td><td>1</td></tr></table><a rel="next" href="/tenders">next</a>'
    client = FakeClient({"https://example.gov.pk/tenders": loop})
    assert len(list(TenderSource(make_settings(), client, {**CFG, "detail": {}}).iter_tenders())) == 1
    assert client.requested == ["https://example.gov.pk/tenders"]       # did not loop forever


def test_page_failure_raises_after_yielding_earlier_rows():
    client = FakeClient({"https://example.gov.pk/tenders": page("listing_page1.html")})
    src, got = TenderSource(make_settings(), client, {**CFG, "detail": {}}), []
    try:
        for r in src.iter_tenders():
            got.append(r)
    except Exception as e:  # noqa: BLE001
        assert "404" in str(e)
    assert len(got) == 3


def test_gbppra_style_links_mode_with_page_param_and_detail_pages():
    cfg = {"base_url": "https://www.gbppra.gov.pk", "listing_urls": ["/viewall"],
           "listing": {"mode": "links", "link_pattern": "procurementdetails", "page_param": "page"},
           "detail": {"enabled": True, "mode": "auto"}}
    p2 = '<a href="/procurementdetails/9001">x tender nine</a>'
    client = FakeClient({
        "https://www.gbppra.gov.pk/viewall": page("listing_cards.html"),
        "https://www.gbppra.gov.pk/viewall?page=2": p2,
        "https://www.gbppra.gov.pk/viewall?page=3": "<html>no links</html>",
        "https://www.gbppra.gov.pk/procurementdetails/3196": page("detail_labels.html"),
        "https://www.gbppra.gov.pk/procurementdetails/2722": page("detail_labels.html").replace("TSE-202511222144", "TSE-2"),
        "https://www.gbppra.gov.pk/procurementdetails/9001": page("detail_labels.html").replace("TSE-202511222144", "TSE-9001"),
    })
    rows = list(TenderSource(make_settings(max_pages=10), client, cfg).iter_tenders())
    assert [r.reference_number for r in rows] == ["TSE-202511222144", "TSE-2", "TSE-9001"]
    assert rows[0].department.startswith("Secretary Water") and rows[0].closing_date == "26 December, 2025"
    assert "https://www.gbppra.gov.pk/viewall?page=3" in client.requested and \
           "https://www.gbppra.gov.pk/viewall?page=4" not in client.requested      # stopped at the empty page


def test_repeated_page_stops_pagination():
    cfg = {"base_url": "https://s.pk", "listing_urls": ["/viewall"], "listing": {"mode": "links", "page_param": "page"}, "detail": {}}
    same = '<a href="/procurementdetails/1">Some tender title</a>'
    client = FakeClient({"https://s.pk/viewall": same, "https://s.pk/viewall?page=2": same})
    assert len(list(TenderSource(make_settings(max_pages=50), client, cfg).iter_tenders())) == 1
