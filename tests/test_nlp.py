import json

import pytest
from sqlalchemy import select

from app.ingest import upsert_tender
from app.models import Category, Keyword, ProcessingStatus, Tender
from app.nlp.extractors import TfidfExtractor, YakeExtractor, get_extractor
from app.nlp.pipeline import mark_for_reprocess, process_tender, run_pending_nlp
from tests.conftest import DATA
from tests.helpers import raw

SAMPLES = json.loads((DATA / "keyword_samples.json").read_text())


def test_taxonomy_classifies_each_sample(taxonomy):
    for s in SAMPLES:
        top = taxonomy.classify(s["title"], s["text"])
        assert top[0].name in s["categories"], (s["title"], [m.name for m in top])


def test_taxonomy_fallback_and_title_weight(taxonomy):
    assert taxonomy.classify("Zzz qqq", "nothing relevant here")[0].name == "Other"
    assert taxonomy.classify("Miscellaneous", "")[0].name == "General Supplies and Mixed Items"
    assert taxonomy.classify("Laptops", "")[0].score > taxonomy.classify("", "laptops")[0].score


def test_word_boundaries(taxonomy):
    names = [m.name for m in taxonomy.classify("Carpet cleaning", "")]
    assert "Vehicles and Transport" not in names       # 'car' must not match inside 'carpet'


@pytest.mark.parametrize("cls", [YakeExtractor, TfidfExtractor])
def test_extractors_return_meaningful_keywords(cls):
    text = SAMPLES[0]["title"] + ". " + SAMPLES[0]["text"] + " Sealed tenders are invited as per PPRA rules."
    kws = cls().extract(text, 10)
    terms = " ".join(k.term for k in kws)
    assert kws and all(0 <= k.score <= 1 for k in kws)
    assert "laptop" in terms or "printer" in terms
    assert not any(k.term in ("tenders", "ppra", "rules") for k in kws)   # boilerplate filtered


def test_unknown_method():
    with pytest.raises(ValueError):
        get_extractor("magic")


def test_pipeline_stores_keywords_categories_and_traceability(session, taxonomy, settings):
    upsert_tender(session, raw(title=SAMPLES[0]["title"], description=SAMPLES[0]["text"]))
    session.commit()
    res = run_pending_nlp(session, taxonomy=taxonomy, settings=settings)
    assert res == {"processed": 1, "failed": 0}
    t = session.scalar(select(Tender))
    assert t.description == SAMPLES[0]["text"]                       # original text preserved
    ranked = sorted(t.category_links, key=lambda c: -c.score)
    assert ranked[0].category.name == "IT Equipment and Computer Hardware"      # primary category
    assert ranked[0].matched_terms and len(ranked) <= 3
    assert t.keyword_links and all(k.method.startswith("yake") and "taxonomy-" in k.method for k in t.keyword_links)
    assert any(k.snippet for k in t.keyword_links)
    assert t.processing.nlp_status == "done" and t.processing.nlp_method
    assert session.scalar(select(Category).where(Category.name == "Other")) is not None


def test_second_run_does_nothing_until_content_changes(session, taxonomy, settings):
    upsert_tender(session, raw(title="Supply of laptops", description="laptops")); session.commit()
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings)
    assert run_pending_nlp(session, taxonomy=taxonomy, settings=settings) == {"processed": 0, "failed": 0}
    upsert_tender(session, raw(title="Road construction and bridge repair", description="road construction and bridge repair")); session.commit()
    assert run_pending_nlp(session, taxonomy=taxonomy, settings=settings)["processed"] == 1
    t = session.scalar(select(Tender))
    assert max(t.category_links, key=lambda c: c.score).category.name == "Construction and Road Maintenance"
    assert session.scalar(select(Keyword).where(Keyword.term == "laptops")) is not None  # vocabulary kept, link removed
    assert "laptops" not in [k.keyword.term for k in t.keyword_links]


def test_forced_reprocess(session, taxonomy, settings):
    upsert_tender(session, raw()); session.commit()
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings)
    assert mark_for_reprocess(session) == 1
    assert run_pending_nlp(session, taxonomy=taxonomy, settings=settings)["processed"] == 1


class Boom(YakeExtractor):
    def extract(self, text, top_n=15):
        if "poison" in text:
            raise RuntimeError("model exploded")
        return super().extract(text, top_n)


def test_failure_is_isolated_recorded_and_retried(session, taxonomy, settings):
    upsert_tender(session, raw(ref="A", title="poison pill laptops")); upsert_tender(session, raw(ref="B", title="Supply of laptops"))
    session.commit()
    ex = Boom()
    res = run_pending_nlp(session, ex, taxonomy, settings)
    assert res == {"processed": 1, "failed": 1}
    bad = session.scalar(select(ProcessingStatus).where(ProcessingStatus.nlp_status == "failed"))
    assert bad.attempts == 1 and "model exploded" in bad.last_error
    run_pending_nlp(session, ex, taxonomy, settings); run_pending_nlp(session, ex, taxonomy, settings)
    assert bad.attempts == settings.max_nlp_attempts
    assert run_pending_nlp(session, ex, taxonomy, settings) == {"processed": 0, "failed": 0}   # gives up, no endless loop


def test_scores_are_plain_python_floats_not_numpy():
    """psycopg2 cannot store numpy.float64 ('schema "np" does not exist'); SQLite hides this, so check the type itself."""
    text = SAMPLES[0]["title"] + ". " + SAMPLES[0]["text"]
    for ex in (YakeExtractor(), TfidfExtractor()):
        kws = ex.extract(text, 10)
        assert kws and all(type(k.score) is float for k in kws), ex.name


def test_pipeline_stores_plain_floats(session, taxonomy, settings):
    upsert_tender(session, raw(title=SAMPLES[0]["title"], description=SAMPLES[0]["text"])); session.commit()
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings)
    t = session.scalar(select(Tender))
    assert all(type(k.score) is float for k in t.keyword_links) and all(type(c.score) is float for c in t.category_links)


def test_pdf_header_and_scanner_stamps_are_removed_from_text(session):
    from app.models import TenderDocument
    from app.nlp.pipeline import build_text
    t = upsert_tender(session, raw(description="d")).tender
    t.documents.append(TenderDocument(url="http://x/a.pdf", text="Public Procurement Regulatory Authority (PPRA)\nScanned with CamScanner\nSupply of laptops"))
    _, body = build_text(t)
    assert "CamScanner" not in body and "Regulatory Authority" not in body and "laptops" in body


def test_stray_mentions_do_not_create_categories(taxonomy):
    """'GB PPRA website' in a PDF footer must not make a media tender a software tender."""
    body = "Interested firms may visit the GB PPRA website for details. Hiring of media agencies for child protection."
    names = [m.name for m in taxonomy.classify("Hiring of Media Houses", body)]
    assert "Software and Web Development" not in names


def test_real_world_titles_from_gbppra(taxonomy):
    cases = {
        "Purchase of Misc. Items & Biomedical Equipments": "Medical Equipment and Healthcare Supplies",
        "Procurement of Lab Reagents and X-Ray Films": "Medical Equipment and Healthcare Supplies",
        "General Overhauling of Govt. Vehicle registration No-GLT B-159": "Vehicles and Transport",
        "Auction of unserviceable materials": "Auction and Disposal",
        "Construction of support walls for feeder channel 160 KW HPS Birgal.": "Construction and Road Maintenance",
        "Supply of Sports Gears/Equipments & Printing of Promotional Material": "Sports and Culture",
        "Construction of Water Channel at Gindai and Water Tank at Gindai Spring": "Water Supply and Irrigation",
    }
    for title, expected in cases.items():
        assert expected in [m.name for m in taxonomy.classify(title, "")], title


def test_pdf_noise_variants_removed():
    from app.nlp.pipeline import _NOISE
    for t in ("Public Procurement Regulatory Authority (PPRA)", "Public  Procurement\nRegulatory Authority(PPRA)", "CamScanner", "Scanned with CamScanner"):
        assert _NOISE.sub("", t).strip() == "", t


def test_boilerplate_phrases_are_not_keywords():
    kws = YakeExtractor().extract("Repair of transport. Procurement Regulatory Authority. Misc items. Executive Engineer Division.", 15)
    terms = [k.term for k in kws]
    assert "procurement regulatory authority" not in terms and "misc" not in terms and "items" not in terms


def test_sitewide_text_stored_as_description_is_ignored_by_nlp(session):
    """Older scrapes stored the site-wide meta text as description; it must not reach keyword extraction."""
    from app.nlp.pipeline import build_text
    t = upsert_tender(session, raw(description="Public Procurement Regulatory Authority (PPRA)")).tender
    _, body = build_text(t)
    assert body.strip() == ""


def test_title_phrases_rank_above_office_letterhead_phrases(session, taxonomy, settings):
    """GB-PPRA PDFs repeat the issuing office in the letterhead; it must not outrank what is being bought."""
    header = "Office of the Deputy Director Education Diamer. Deputy Director Education Diamer. " * 4
    t = upsert_tender(session, raw(title="Repair of Transport", dept="Deputy Director Education Office Diamer",
                                   description=header + "Sealed tenders are invited for repair of transport.")).tender
    session.commit()
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings)
    terms = [k.keyword.term for k in sorted(t.keyword_links, key=lambda k: -k.score)]
    assert terms[0] == "repair of transport"
    assert "deputy director education" not in terms and "director education diamer" not in terms


def test_agriculture_and_civil_work_titles(taxonomy):
    assert "Agriculture, Forestry and Environment" in [m.name for m in taxonomy.classify("Transportation of Wheat Seed stock", "")]
    assert "Agriculture, Forestry and Environment" in [m.name for m in taxonomy.classify("Establishment of Cattle Shed and Purchae of Milk Cow", "")]
    assert "Construction and Road Maintenance" in [m.name for m in taxonomy.classify("Civil Work", "")]


REAL = json.loads((DATA / "real_titles.json").read_text())


def test_real_gbppra_titles_get_an_acceptable_category(taxonomy):
    """52 real titles. NOTE: the taxonomy was written while reading these titles, so this is a regression guard,
    not an honest accuracy figure. Measure real accuracy on tenders that were NOT used to build the taxonomy."""
    misses = []
    for r in REAL:
        names = [m.name for m in taxonomy.classify(r["title"], "")]
        if not set(names) & set(r["any_of"]):
            misses.append((r["title"][:70], names))
    assert len(misses) <= 3, misses


def test_notice_boilerplate_never_becomes_a_keyword(session, taxonomy, settings):
    text = (DATA / "nit_bnr_gilgit.txt").read_text()
    from app.models import TenderDocument
    from app.normalize import sha256
    from app.ingest import compute_content_hash
    t = upsert_tender(session, raw(title="Const. of 600 feet road at Sakwar Gilgit", dept="XEN B&R Division Gilgit")).tender
    t.documents.append(TenderDocument(url="http://x/a.pdf", status="ok", text=text, text_hash=sha256(text)))
    t.content_hash = compute_content_hash(t)
    session.commit()
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings)
    terms = [k.keyword.term for k in t.keyword_links]
    for bad in ("pakistan engineering council", "work estimate cost", "const", "bid security", "estimate cost", "gilgit-baltistan office"):
        assert not any(bad in x for x in terms), (bad, terms)
    assert "sakwar gilgit" in terms and any("road" in x for x in terms)
    assert max(t.category_links, key=lambda c: c.score).category.name == "Construction and Road Maintenance"
