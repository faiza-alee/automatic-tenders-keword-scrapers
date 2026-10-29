from app.scraper.parser import looks_like_captcha, parse_detail, parse_listing
from tests.conftest import DATA

BASE = "https://example.gov.pk/tenders"


def read(name):
    return (DATA / name).read_text(encoding="utf-8")


def test_auto_listing_maps_columns_and_links():
    rows, nxt = parse_listing(read("listing_page1.html"), BASE, {"mode": "auto"})
    assert len(rows) == 3
    r = rows[0]
    assert r.reference_number == "AB/Cash/Notice/522" and r.title.startswith("Repair of link road")
    assert r.department == "Executive Engineer B&R Division C&WD Gilgit"
    assert r.publication_date == "10-05-2026" and r.closing_date == "30-12-2099" and r.status == "Open"
    assert r.document_urls == ["https://example.gov.pk/doc/tender-522.pdf"]
    assert r.source_url == "https://example.gov.pk/doc/tender-522.pdf"      # no detail page -> the document
    assert rows[1].source_url == "https://example.gov.pk/tender/gbh-1-5" and rows[1].document_urls == []
    assert nxt == "https://example.gov.pk/tenders?page=2"


def test_auto_listing_other_header_names():
    rows, nxt = parse_listing(read("listing_page2.html"), BASE, {})
    assert rows[0].reference_number == "HW-9" and rows[0].department == "IT Board GB"
    assert rows[0].title.startswith("Development of web portal") and rows[0].closing_date == "2099-02-01"
    assert nxt is None


def test_css_mode():
    cfg = {"mode": "css", "row": "div.item", "next_page": "a.nxt", "fields": {
        "reference_number": ".no", "title": "h3.t", "department": ".dept", "closing_date": ".closing"}}
    rows, nxt = parse_listing(read("listing_css.html"), BASE, cfg)
    assert [r.reference_number for r in rows] == ["X-1", "X-2"]
    assert rows[0].source_url == "https://example.gov.pk/t/x-1" and rows[1].source_url is None
    assert nxt == "https://example.gov.pk/page/2"


def test_garbage_page_returns_no_rows():
    rows, nxt = parse_listing("<html><body><p>nothing</p></body></html>", BASE, {})
    assert rows == [] and nxt is None


def test_detail_auto_and_docs():
    d = parse_detail(read("detail.html"), "https://example.gov.pk/t/1", {"mode": "auto"})
    assert d["description"].startswith("Metaling") and d["eligibility"].startswith("PEC")
    assert d["closing_date"] == "30 Dec 2099"
    assert d["document_urls"] == ["https://example.gov.pk/files/bid-docs.pdf", "https://example.gov.pk/files/boq.xlsx"]


def test_detail_css_fields():
    html = '<div class="d">Hello <b>world</b></div><a id="x" href="/f.pdf">f</a>'
    d = parse_detail(html, "https://e.pk/", {"mode": "css", "fields": {"description": "div.d"}})
    assert d["description"] == "Hello world" and d["document_urls"] == ["https://e.pk/f.pdf"]


def test_captcha_detection():
    assert looks_like_captcha('<div class="g-recaptcha"></div>')
    assert not looks_like_captcha("<html>normal</html>")


def test_links_mode_finds_detail_links_regardless_of_layout():
    cfg = {"mode": "links", "link_pattern": "procurementdetails"}
    rows, _ = parse_listing(read("listing_cards.html"), "https://www.gbppra.gov.pk/viewall", cfg)
    assert [r.source_url for r in rows] == ["https://www.gbppra.gov.pk/procurementdetails/3196",
                                           "https://www.gbppra.gov.pk/procurementdetails/2722"]   # duplicate link ignored
    assert rows[0].title is None and rows[1].title == "Dairy Animals"                              # 'View Details' is not a title


def test_detail_label_value_pairs_gbppra_style():
    d = parse_detail(read("detail_labels.html"), "https://www.gbppra.gov.pk/procurementdetails/2144", {"mode": "auto"})
    assert d["reference_number"] == "TSE-202511222144"
    assert d["title"].startswith("ICB-01 EPC/Turnkey") and d["description"].startswith("The Water and Power")
    assert d["category"] == "Tender Notice" and d["department"].startswith("Secretary Water")
    assert d["publication_date"] == "22 November, 2025" and d["closing_date"] == "26 December, 2025"
    assert d["document_urls"] == ["https://www.gbppra.gov.pk/storage/proc_files/notice-abc",
                                  "https://www.gbppra.gov.pk/storage/proc_files/bidding-xyz.pdf"]


def test_opening_date_is_not_mistaken_for_closing_date():
    rows, _ = parse_listing(read("table_opening_closing.html"), "https://www.gbppra.gov.pk/procurements/1", {"mode": "auto"})
    assert rows[0].publication_date == "31-07-2024" and rows[0].closing_date == "15-08-2024"
    assert rows[0].department == "Divisional Forest Officer Darel" and rows[0].category == "Tender Notice"


def test_detail_table_rows_with_an_extra_badge_cell():
    """GB-PPRA rows under objection have a third cell ('In Objection'); the title must still be read."""
    d = parse_detail(read("detail_three_cells.html"), "https://www.gbppra.gov.pk/procurementdetails/3164", {"mode": "auto"})
    assert d["title"].startswith("Infrastructure Development in Chilas") and d["reference_number"] == "TSE-202609263164"
    assert d["department"] == "Conservator of Forests, Diamer-Astore Division" and d["closing_date"] == "02 October, 2026"
    assert len(d["document_urls"]) == 1 and "description" not in d


def test_title_falls_back_to_heading_or_page_title():
    html = "<html><head><title>Site | Supply of widgets</title></head><body><h1>[TSE-1]</h1><h2>Supply of widgets for school</h2></body></html>"
    assert parse_detail(html, "https://x.pk/p/1", {"mode": "auto"})["title"] == "Supply of widgets for school"
    only_tag = "<html><head><title>Site | Supply of widgets</title></head><body><p>x</p></body></html>"
    assert parse_detail(only_tag, "https://x.pk/p/1", {"mode": "auto"})["title"] == "Supply of widgets"


def test_site_wide_meta_description_is_not_used_as_tender_description():
    html = ('<html><head><meta name="description" content="Public Procurement Regulatory Authority (PPRA)"></head><body>'
            '<table><tr><td>Procurment Description</td><td></td><td></td></tr><tr><td>Department</td><td>Health Dept</td></tr></table></body></html>')
    d = parse_detail(html, "https://x.pk/p/1", {"mode": "auto"})
    assert "description" not in d and d["department"] == "Health Dept"
