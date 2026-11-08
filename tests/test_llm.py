import json
import logging

import pytest
import requests
from sqlalchemy import select

from app import cli
from app.ingest import upsert_tender
from app.models import Tender
from app.nlp.extractors import BaseExtractor, ExtractedKeyword, YakeExtractor
from app.nlp.llm import (LLMAnalyzer, LLMBadAnswer, LLMClient, LLMError, LLMFatalError, LLMResult, LLMUnavailable,
                         build_llm_analyzer, category_hints, compare_methods, parse_json)
from app.models import ProcessingStatus
from app.nlp.pipeline import mark_for_reprocess, run_pending_nlp
from tests.conftest import make_settings
from tests.helpers import raw

KEY = "sk-TOPSECRETDEEPSEEKKEY1234567890"


class Resp:
    def __init__(self, status=200, content=None, text="", headers=None, raw_json=None):
        self.status_code, self.text, self.headers = status, text, headers or {}
        self._j = raw_json if raw_json is not None else {"choices": [{"message": {"content": content}}]}

    def json(self):
        return self._j


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def good_json(**over):
    d = {"primary_category": "Construction and Road Maintenance", "other_categories": ["Water Supply and Irrigation"],
         "keywords": ["link road", "retaining wall", "sakwar gilgit"]}
    d.update(over)
    return json.dumps(d)


def client(*responses, **kw):
    sleeps = []
    c = LLMClient(KEY, session=Session(*responses), sleep=sleeps.append, **kw)
    c.sleeps = sleeps
    return c


CATS = [("Construction and Road Maintenance", "e.g. road"), ("Water Supply and Irrigation", "e.g. water supply"),
        ("Vehicles and Transport", "e.g. vehicles"), ("Results and Notices", "e.g. evaluation report")]


def analyzer(*responses, **kw):
    return LLMAnalyzer(client(*responses), CATS, "Other", **kw)


# ------------------------------------------------------------------ JSON parsing
@pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```', 'Sure! Here you go:\n{"a": 1}\nHope it helps',
                                  '<think>hmm {not json}</think>{"a": 1}'])
def test_parse_json_variants(text):
    assert parse_json(text) == {"a": 1}


@pytest.mark.parametrize("text", ["no json here", '{"a": ', "[1, 2]"])
def test_parse_json_rejects_garbage(text):
    with pytest.raises(LLMError):
        parse_json(text)


# ------------------------------------------------------------------ HTTP client
def test_request_format():
    c = client(Resp(200, "{}"))
    c.chat("sys", "user text")
    url, kw = c.session.calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert kw["headers"]["Authorization"] == f"Bearer {KEY}"
    b = kw["json"]
    assert b["model"] == "deepseek-flash" and b["temperature"] == 0 and b["response_format"] == {"type": "json_object"}
    assert b["thinking"] == {"type": "disabled"}                      # V4 models think by default; we want a short JSON answer
    assert [m["role"] for m in b["messages"]] == ["system", "user"] and b["messages"][1]["content"] == "user text"


def test_retries_busy_server_and_network_errors():
    c = client(Resp(503), requests.ConnectionError("down"), Resp(429, headers={"Retry-After": "2"}), Resp(200, "{}"), max_retries=4)
    assert c.chat("s", "u") == "{}" and 2 in c.sleeps and len(c.session.calls) == 4


def test_gives_up_after_retries():
    with pytest.raises(LLMError):
        client(Resp(500), Resp(500), Resp(500)).chat("s", "u")


def test_bad_key_and_no_balance_are_fatal_and_never_leak_the_key(caplog):
    with pytest.raises(LLMFatalError) as e:
        client(Resp(401, text=f"bad key {KEY}")).chat("s", "u")
    assert KEY not in str(e.value) and "401" in str(e.value)
    with pytest.raises(LLMFatalError) as e2:
        client(Resp(402, text="Insufficient Balance")).chat("s", "u")
    assert "balance" in str(e2.value).lower()
    with pytest.raises(LLMError) as e3:
        client(Resp(404, text=f"model not found for key {KEY}")).chat("s", "u")
    assert KEY not in str(e3.value) and "***" in str(e3.value)
    assert KEY not in caplog.text


def test_json_mode_is_dropped_if_the_model_rejects_it():
    c = client(Resp(400, text="response_format is not supported"), Resp(200, "{}"))
    assert c.chat("s", "u") == "{}"
    assert "response_format" in c.session.calls[0][1]["json"] and "response_format" not in c.session.calls[1][1]["json"]


@pytest.mark.parametrize("resp", [Resp(200, content=None), Resp(200, content="   "), Resp(200, raw_json={"oops": 1})])
def test_empty_or_odd_answers_raise(resp):
    with pytest.raises(LLMError):
        client(resp).chat("s", "u")


def test_settings_never_print_the_key():
    s = make_settings(deepseek_api_key=KEY)
    assert KEY not in repr(s) and KEY not in str(s) and KEY not in s.model_dump_json()
    assert s.deepseek_api_key.get_secret_value() == KEY


# ------------------------------------------------------------------ analyzer
def test_analyzer_cleans_the_models_answer():
    a = analyzer(Resp(200, good_json(
        primary_category="construction and road maintenance",                      # case-insensitive match
        other_categories=["Water Supply and Irrigation", "Invented Category", "Other", "Construction and Road Maintenance"],
        keywords=["Link Road ", "link road", "bid security", "the quick brown fox jumps over", "x", "retaining wall", "tender"])))
    r = a.analyze("Link road", "XEN", "text")
    assert r.categories == ["Construction and Road Maintenance", "Water Supply and Irrigation"]   # invented/duplicate/Other dropped
    assert r.keywords == ["link road", "retaining wall"]                                         # deduped, boilerplate and long phrases dropped
    assert a.version == "llm-deepseek-flash"


def test_category_outside_the_taxonomy_is_rejected():
    with pytest.raises(LLMError):
        analyzer(Resp(200, good_json(primary_category="Space Travel"))).analyze("t", None, "b")


def test_other_is_an_allowed_primary_category():
    assert analyzer(Resp(200, good_json(primary_category="Other", other_categories=[]))).analyze("t", None, "b").categories == ["Other"]


def test_prompt_contains_categories_title_and_a_trimmed_excerpt_but_never_the_key():
    a = analyzer(Resp(200, "{}"), max_chars=50)
    p = a.build_prompt("Link road at Sakwar", "XEN B&R Gilgit", "§" * 500)
    assert "Construction and Road Maintenance" in p and "Other: none of the above" in p
    assert "Link road at Sakwar" in p and "XEN B&R Gilgit" in p and p.count("§") == 50 and "json" in p.lower() and KEY not in p


def test_circuit_breaker_and_fatal_errors_stop_hammering_the_api():
    now = [0.0]
    a = LLMAnalyzer(client(*[Resp(200, "not json")] * 10), CATS, clock=lambda: now[0], max_failures=3, cooldown=600)
    for _ in range(3):
        with pytest.raises(LLMError):
            a.analyze("t", None, "b")
    calls = len(a.client.session.calls)
    with pytest.raises(LLMError, match="band"):
        a.analyze("t", None, "b")
    assert len(a.client.session.calls) == calls                  # no new request while it is switched off
    now[0] = 601
    with pytest.raises(LLMError):
        a.analyze("t", None, "b")
    assert len(a.client.session.calls) > calls                   # tries again after the cooldown

    b = LLMAnalyzer(client(Resp(402)), CATS, clock=lambda: now[0])
    with pytest.raises(LLMFatalError):
        b.analyze("t", None, "b")
    with pytest.raises(LLMError, match="band"):
        b.analyze("t", None, "b")                                # one fatal error is enough to pause


# ------------------------------------------------------------------ building it from settings
def test_build_analyzer_from_settings(taxonomy, caplog):
    assert build_llm_analyzer(make_settings(use_llm=False, deepseek_api_key=KEY), taxonomy) is None
    caplog.set_level(logging.WARNING)
    assert build_llm_analyzer(make_settings(use_llm=True, deepseek_api_key=None), taxonomy) is None
    assert "DEEPSEEK_API_KEY" in caplog.text
    a = build_llm_analyzer(make_settings(use_llm=True, deepseek_api_key=KEY), taxonomy)
    assert isinstance(a, LLMAnalyzer) and {n for n, _ in a.categories} == {c["name"] for c in taxonomy.categories}
    assert KEY not in repr(a)
    assert all(h.startswith("e.g. ") for _, h in category_hints(taxonomy))


# ------------------------------------------------------------------ pipeline
class FakeLLM:
    version = "llm-deepseek-flash"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def analyze(self, title, department, body):
        self.calls.append((title, department, body))
        if self.error:
            raise self.error
        return self.result


def test_llm_decides_category_and_keywords(session, taxonomy, settings):
    upsert_tender(session, raw(ref="A", title="Capacity Building of Youth in digital skills", dept="IT Department", description="Training of youth"))
    session.commit()
    llm = FakeLLM(LLMResult(["Education and Training", "Consultancy and Professional Services"],
                            ["digital skills", "youth training", "capacity building"]))
    assert run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=llm) == {"processed": 1, "failed": 0}
    t = session.scalar(select(Tender))
    assert [(c.category.name, c.score) for c in sorted(t.category_links, key=lambda c: -c.score)] == [
        ("Education and Training", 1.0), ("Consultancy and Professional Services", 0.7)]
    assert [k.keyword.term for k in sorted(t.keyword_links, key=lambda k: -k.score)] == ["digital skills", "youth training", "capacity building"]
    assert t.processing.nlp_method == "llm-deepseek-flash+taxonomy-" + taxonomy.version
    assert all(type(k.score) is float for k in t.keyword_links) and all(type(c.score) is float for c in t.category_links)
    assert llm.calls[0][1] == "IT Department"                    # the model is told who issued it, and the title


def test_llm_other_is_stored_with_score_zero_and_few_keywords_are_topped_up(session, taxonomy, settings):
    upsert_tender(session, raw(ref="A", title="Supply of laptops and printers"))
    session.commit()
    run_pending_nlp(session, YakeExtractor(), taxonomy, settings, llm=FakeLLM(LLMResult(["Other"], ["laptops"])))
    t = session.scalar(select(Tender))
    assert [(c.category.name, c.score) for c in t.category_links] == [("Other", 0.0)]
    terms = [k.keyword.term for k in t.keyword_links]
    assert "laptops" in terms and len(terms) >= 3                # the model gave 1, the title's own phrases fill in


def test_pipeline_falls_back_when_the_llm_fails(session, taxonomy, settings):
    upsert_tender(session, raw(ref="A", title="Repair of transport vehicles", description="Repair of vehicles and spare parts."))
    session.commit()
    res = run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=FakeLLM(error=LLMError("DeepSeek tak nahi pohancha")))
    assert res == {"processed": 1, "failed": 0}                  # the tender is still processed
    t = session.scalar(select(Tender))
    assert t.processing.nlp_method.startswith("yake-") and t.keyword_links
    assert max(t.category_links, key=lambda c: c.score).category.name == "Vehicles and Transport"


def test_a_wrong_keyword_method_does_not_stop_processing(session, taxonomy, caplog):
    upsert_tender(session, raw(ref="A", title="Repair of transport"))
    session.commit()
    res = run_pending_nlp(session, taxonomy=taxonomy, settings=make_settings(keyword_method="hf"))
    assert res == {"processed": 1, "failed": 0} and "bad KEYWORD_METHOD" in caplog.text


# ------------------------------------------------------------------ CLI + comparison
def test_llm_check_without_a_key_explains_what_to_do(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: make_settings(deepseek_api_key=None))
    assert cli.main(["llm-check"]) == 1
    assert "NAHI MILA" in capsys.readouterr().out


def test_llm_check_reports_success_and_failure_without_the_key(monkeypatch, capsys):
    import app.nlp.llm as llm_mod
    monkeypatch.setattr(cli, "get_settings", lambda: make_settings(deepseek_api_key=KEY, taxonomy_path="config/taxonomy.yaml"))
    monkeypatch.setattr(llm_mod, "build_llm_analyzer", lambda s, t: FakeLLM(LLMResult(["Construction and Road Maintenance"], ["link road"])))
    assert cli.main(["llm-check"]) == 0
    out = capsys.readouterr().out
    assert "THEEK" in out and "link road" in out and KEY not in out
    monkeypatch.setattr(llm_mod, "build_llm_analyzer", lambda s, t: FakeLLM(error=LLMFatalError("DeepSeek account mein balance khatam hai (HTTP 402)")))
    assert cli.main(["llm-check"]) == 1
    out = capsys.readouterr().out
    assert "NAKAM" in out and "balance" in out and KEY not in out


def test_compare_methods_shows_both_answers_and_saves_nothing(session, taxonomy):
    upsert_tender(session, raw(ref="A", title="Repair of transport vehicles"))
    upsert_tender(session, raw(ref="B", title="Metalling of road"))
    session.commit()

    class Mixed:
        def analyze(self, title, department, body):
            if "road" in title.lower():
                raise LLMError("boom")
            return LLMResult(["Vehicles and Transport"], ["repair"])

    rows = compare_methods(session, Mixed(), taxonomy, n=10)
    by = {r["title"]: r for r in rows}
    assert by["Repair of transport vehicles"]["llm"] == by["Repair of transport vehicles"]["taxonomy"] == "Vehicles and Transport"
    assert by["Metalling of road"]["llm"] is None and "boom" in by["Metalling of road"]["error"]
    assert session.scalar(select(Tender).where(Tender.reference_number == "A")).keyword_links == []   # nothing was saved


# ------------------------------------------------------------------ the LLM is paid: once per tender, only new/changed ones
class Scripted:
    """An LLM whose answers are scripted, and which counts how often it is called."""
    version = "llm-deepseek-flash"

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def analyze(self, title, department, body):
        self.calls += 1
        o = self.outcomes.pop(0) if self.outcomes else LLMResult(["Vehicles and Transport"], ["repair of transport", "vehicles", "spare parts"])
        if isinstance(o, Exception):
            raise o
        return o


def ps_of(session, ref):
    return session.scalar(select(Tender).where(Tender.reference_number == ref)).processing


def test_an_old_tender_is_never_sent_to_the_llm_again(session, taxonomy, settings):
    upsert_tender(session, raw(ref="A", title="Repair of transport", description="d1"))
    session.commit()
    llm = Scripted()
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=llm)
    assert llm.calls == 1 and ps_of(session, "A").nlp_status == "done"
    for _ in range(3):                                           # three more scheduled runs, nothing changed
        upsert_tender(session, raw(ref="A", title="Repair of transport", description="d1"))
        session.commit()
        run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=llm)
    assert llm.calls == 1                                        # still ONE call in total
    upsert_tender(session, raw(ref="A", title="Repair of transport", description="d2 changed"))   # the tender really changed
    session.commit()
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=llm)
    assert llm.calls == 2


def test_only_new_tenders_are_sent_not_the_ones_processed_before(session, taxonomy, settings):
    upsert_tender(session, raw(ref="OLD", title="Repair of transport"))
    session.commit()
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings)               # processed before USE_LLM existed
    upsert_tender(session, raw(ref="NEW", title="Metalling of road"))
    session.commit()
    llm = Scripted(LLMResult(["Construction and Road Maintenance"], ["metalling of road", "road", "metalling"]))
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=llm)
    assert llm.calls == 1                                                          # only the new tender
    assert ps_of(session, "OLD").nlp_method.startswith("yake-") and ps_of(session, "NEW").nlp_method.startswith("llm-")


def test_when_deepseek_is_down_the_tender_waits_and_gets_the_llm_later(session, taxonomy, settings):
    upsert_tender(session, raw(ref="A", title="Repair of transport vehicles"))
    session.commit()
    down = Scripted(LLMUnavailable("DeepSeek tak nahi pohancha"))
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=down)
    ps = ps_of(session, "A")
    assert ps.nlp_status == "pending" and ps.attempts == 0 and ps.nlp_method.startswith("yake-")   # classic result stored, not used up
    t = session.scalar(select(Tender))
    assert t.keyword_links and t.category_links                                    # the API already shows something
    up = Scripted()
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=up)         # next run: DeepSeek is back
    assert up.calls == 1 and ps_of(session, "A").nlp_status == "done" and ps_of(session, "A").nlp_method.startswith("llm-")
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=up)
    assert up.calls == 1


def test_no_balance_does_not_use_up_attempts_however_long_it_lasts(session, taxonomy, settings):
    upsert_tender(session, raw(ref="A", title="Repair of transport"))
    session.commit()
    for _ in range(6):
        run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=Scripted(LLMFatalError("balance khatam (HTTP 402)")))
    assert ps_of(session, "A").nlp_status == "pending" and ps_of(session, "A").attempts == 0


def test_an_unusable_answer_is_retried_at_most_max_attempts_times(session, taxonomy, settings):
    upsert_tender(session, raw(ref="A", title="Repair of transport"))
    session.commit()
    llm = Scripted(*[LLMBadAnswer("category bahar ki")] * 10)
    for i in range(1, 6):
        run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=llm)
    assert llm.calls == settings.max_nlp_attempts == 3                              # then it stops paying for this tender
    assert ps_of(session, "A").nlp_status == "done" and ps_of(session, "A").attempts == 3
    assert ps_of(session, "A").nlp_method.startswith("yake-")


def test_per_run_cap_protects_against_a_big_first_run(session, taxonomy):
    for i in range(5):
        upsert_tender(session, raw(ref=f"T{i}", title=f"Repair of transport {i}"))
    session.commit()
    s = make_settings(llm_max_per_run=2)
    llm = Scripted()
    assert run_pending_nlp(session, taxonomy=taxonomy, settings=s, llm=llm)["processed"] == 2 and llm.calls == 2
    assert run_pending_nlp(session, taxonomy=taxonomy, settings=s, llm=llm)["processed"] == 2
    assert run_pending_nlp(session, taxonomy=taxonomy, settings=s, llm=llm)["processed"] == 1 and llm.calls == 5
    assert run_pending_nlp(session, taxonomy=taxonomy, settings=s, llm=llm)["processed"] == 0 and llm.calls == 5


def test_reprocess_missing_llm_marks_only_tenders_the_llm_never_handled(session, taxonomy, settings):
    upsert_tender(session, raw(ref="OLD", title="Repair of transport"))
    session.commit()
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings)
    upsert_tender(session, raw(ref="NEW", title="Metalling of road"))
    session.commit()
    run_pending_nlp(session, taxonomy=taxonomy, settings=settings, llm=Scripted(LLMResult(["Construction and Road Maintenance"], ["metalling of road", "road", "metalling"])))
    assert mark_for_reprocess(session, only_without_llm=True) == 1
    assert ps_of(session, "OLD").nlp_status == "pending" and ps_of(session, "NEW").nlp_status == "done"


def test_reprocess_all_asks_for_confirmation_when_the_llm_is_on(session_factory, taxonomy, monkeypatch, capsys):
    import app.db
    import app.nlp.pipeline as pipe
    with session_factory() as s:
        upsert_tender(s, raw(ref="A", title="Repair of transport")); upsert_tender(s, raw(ref="B", title="Metalling of road"))
        s.commit()
        run_pending_nlp(s, taxonomy=taxonomy, settings=make_settings())
    on = make_settings(use_llm=True, deepseek_api_key=KEY, taxonomy_path="config/taxonomy.yaml")
    llm = Scripted()
    monkeypatch.setattr(cli, "get_settings", lambda: on)
    monkeypatch.setattr(app.db, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(pipe, "build_llm_analyzer", lambda s, t: llm)
    assert cli.main(["reprocess", "--all"]) == 2                                   # refuses: it would cost 2 calls
    out = capsys.readouterr().out
    assert "2 paid calls" in out and "--yes" in out and llm.calls == 0 and KEY not in out
    with session_factory() as s:
        assert {ps.nlp_status for ps in s.scalars(select(ProcessingStatus))} == {"done"}   # nothing was touched
    assert cli.main(["reprocess", "--all", "--yes"]) == 0 and llm.calls == 2


def test_thinking_switch_is_dropped_and_more_room_given_if_the_model_rejects_it():
    c = client(Resp(400, text="unknown field: thinking"), Resp(200, "{}"))
    assert c.chat("s", "u") == "{}"
    first, second = c.session.calls[0][1]["json"], c.session.calls[1][1]["json"]
    assert "thinking" in first and "thinking" not in second and second["max_tokens"] >= 2500


def test_a_401_tells_the_user_how_to_make_a_changed_env_take_effect():
    with pytest.raises(LLMFatalError) as e:
        client(Resp(401, text="Authentication Fails")).chat("s", "u")
    assert "force-recreate" in str(e.value) and KEY not in str(e.value)
