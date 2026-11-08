import logging
import re
import zlib

import numpy as np
import pytest
import requests
from sqlalchemy import select

from app import cli
from app.ingest import upsert_tender
from app.models import Tender
from app.nlp.extractors import BaseExtractor, YakeExtractor, get_extractor
from app.nlp.hf_embed import (FallbackExtractor, HFEmbeddingClient, HFEmbedKeywordExtractor, HFError,
                              build_hf_extractor)
from app.nlp.pipeline import run_pending_nlp
from tests.conftest import make_settings
from tests.helpers import raw

SECRET = "hf_TOPSECRETTOKEN1234567890"


class Resp:
    def __init__(self, status=200, data=None, text="", headers=None):
        self.status_code, self._d, self.text, self.headers = status, data, text, headers or {}

    def json(self):
        return self._d


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(*responses, **kw):
    sleeps = []
    c = HFEmbeddingClient(SECRET, session=Session(*responses), sleep=sleeps.append, **kw)
    c.sleeps = sleeps
    return c


class FakeEmbedder:
    """Deterministic bag-of-words vectors: texts sharing words are close, like a (very) poor embedding model."""
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        m = np.zeros((len(texts), 128))
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z]+", t.lower()):
                m[i, zlib.crc32(w.encode()) % 128] += 1
        n = np.linalg.norm(m, axis=1, keepdims=True)
        n[n == 0] = 1
        return m / n


# ---------------------------------------------------------------- HTTP client
def test_embed_shapes_and_normalisation():
    c = client(Resp(200, [[3.0, 4.0], [0.0, 2.0]]))
    m = c.embed(["a", "b"])
    assert m.shape == (2, 2) and np.allclose(np.linalg.norm(m, axis=1), 1)
    assert np.allclose(client(Resp(200, [[1.0, 0.0]])).embed(["a"]), [[1.0, 0.0]])
    assert client(Resp(200, [1.0, 1.0])).embed(["a"]).shape == (1, 2)                    # a single flat vector
    token_level = [[[1.0, 0.0], [3.0, 0.0]]]                                             # token vectors get averaged
    assert np.allclose(client(Resp(200, token_level)).embed(["a"]), [[1.0, 0.0]])


def test_bad_response_shapes_raise():
    with pytest.raises(HFError):
        client(Resp(200, {"error": "boom"})).embed(["a"])
    with pytest.raises(HFError):
        client(Resp(200, [[1.0, 2.0]])).embed(["a", "b"])           # 1 vector for 2 texts


def test_request_format_and_batching():
    c = client(Resp(200, [[1.0, 0.0]] * 2), Resp(200, [[0.0, 1.0]]), batch_size=2)
    c.embed(["a", "b", "c"])
    url, kw = c.session.calls[0]
    assert url.endswith("/BAAI/bge-small-en-v1.5/pipeline/feature-extraction")
    assert kw["json"] == {"inputs": ["a", "b"]} and kw["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert len(c.session.calls) == 2                                    # 3 texts, batch size 2



def test_retries_on_busy_model_and_network_errors():
    c = client(Resp(503), requests.ConnectionError("down"), Resp(429, headers={"Retry-After": "2"}), Resp(200, [[1.0, 0.0]]), max_retries=4)
    assert c.embed(["a"]).shape == (1, 2)
    assert 2 in c.sleeps and len(c.session.calls) == 4


def test_gives_up_after_retries():
    with pytest.raises(HFError):
        client(Resp(500), Resp(500), Resp(500)).embed(["a"])


def test_other_route_is_tried_when_first_is_404():
    c = client(Resp(404), Resp(200, [[1.0, 0.0]]))
    c.embed(["a"])
    assert c.session.calls[0][0].endswith("/pipeline/feature-extraction") and not c.session.calls[1][0].endswith("/pipeline/feature-extraction")


def test_token_rejection_never_leaks_the_token(caplog):
    c = client(Resp(401, text=f"bad token {SECRET}"))
    with pytest.raises(HFError) as e:
        c.embed(["a"])
    assert SECRET not in str(e.value) and "401" in str(e.value)
    c2 = client(Resp(400, text=f"you sent {SECRET}"))
    with pytest.raises(HFError) as e2:
        c2.embed(["a"])
    assert SECRET not in str(e2.value) and "***" in str(e2.value)       # scrubbed
    assert SECRET not in caplog.text


def test_settings_never_print_the_token():
    s = make_settings(hf_token=SECRET)
    assert SECRET not in repr(s) and SECRET not in str(s) and SECRET not in s.model_dump_json()
    assert s.hf_token.get_secret_value() == SECRET


# ---------------------------------------------------------------- keyword extractor
TEXT = ("Construction of link road and widening of road with retaining wall. The road works include metalling of road, "
        "drainage and a small bridge. Sealed tenders are invited from contractors. Payment will be made in rupees.")


def test_extractor_returns_relevant_distinct_verbatim_phrases():
    ex = HFEmbedKeywordExtractor(FakeEmbedder(), "BAAI/bge-small-en-v1.5")
    kws = ex.extract(TEXT, 6)
    terms = [k.term for k in kws]
    assert 0 < len(kws) <= 6 and len(set(terms)) == len(terms)
    assert all(t in TEXT.lower() for t in terms) and all(0.0 <= k.score <= 1.0 and type(k.score) is float for k in kws)
    assert any("road" in t for t in terms[:3])
    assert ex.version == "hf-bge-small-en-v1.5"


def test_extractor_handles_empty_text():
    assert HFEmbedKeywordExtractor(FakeEmbedder()).extract("", 5) == []
    assert HFEmbedKeywordExtractor(FakeEmbedder()).extract("the of and", 5) == []


# ---------------------------------------------------------------- fallback + circuit breaker
class Broken(BaseExtractor):
    version = "hf-broken"

    def __init__(self):
        self.calls = 0

    def extract(self, text, top_n=15):
        self.calls += 1
        raise HFError("Hugging Face tak nahi pohancha ja saka")


def test_fallback_uses_yake_and_reports_the_method_really_used():
    now = [0.0]
    broken = Broken()
    fb = FallbackExtractor(broken, YakeExtractor(), max_failures=3, cooldown=600, clock=lambda: now[0])
    for _ in range(5):
        assert fb.extract(TEXT, 5) and fb.version.startswith("yake")
    assert broken.calls == 3                      # stopped trying after 3 failures in a row
    now[0] = 601
    fb.extract(TEXT, 5)
    assert broken.calls == 4                      # tries again after the cooldown


def test_fallback_prefers_hugging_face_when_it_works():
    good = HFEmbedKeywordExtractor(FakeEmbedder(), "BAAI/bge-small-en-v1.5")
    fb = FallbackExtractor(good, YakeExtractor())
    assert fb.extract(TEXT, 5) and fb.version == "hf-bge-small-en-v1.5"


# ---------------------------------------------------------------- wiring
def test_factory_without_token_falls_back_to_yake_with_a_warning(caplog):
    caplog.set_level(logging.WARNING)
    assert isinstance(build_hf_extractor(make_settings(hf_token=None)), YakeExtractor)
    assert isinstance(build_hf_extractor(make_settings(hf_token="   ")), YakeExtractor)
    assert "HF_TOKEN" in caplog.text


def test_factory_with_token_builds_the_hf_extractor():
    ex = get_extractor("hf", make_settings(hf_token=SECRET))
    assert isinstance(ex, FallbackExtractor) and isinstance(ex.primary, HFEmbedKeywordExtractor)
    assert SECRET not in repr(ex)


def test_pipeline_records_hf_method_and_survives_an_outage(session, taxonomy, settings):
    upsert_tender(session, raw(ref="A", title="Construction of link road", description=TEXT))
    session.commit()
    good = FallbackExtractor(HFEmbedKeywordExtractor(FakeEmbedder(), "BAAI/bge-small-en-v1.5"), YakeExtractor())
    assert run_pending_nlp(session, good, taxonomy, settings) == {"processed": 1, "failed": 0}
    t = session.scalar(select(Tender))
    assert t.processing.nlp_method.startswith("hf-bge-small-en-v1.5+taxonomy-")
    assert t.keyword_links and all(type(k.score) is float for k in t.keyword_links)

    upsert_tender(session, raw(ref="B", title="Repair of transport vehicles", description="Repair of vehicles and spare parts."))
    session.commit()
    down = FallbackExtractor(Broken(), YakeExtractor())
    assert run_pending_nlp(session, down, taxonomy, settings) == {"processed": 1, "failed": 0}    # no failure, just YAKE
    b = session.scalar(select(Tender).where(Tender.reference_number == "B"))
    assert b.processing.nlp_method.startswith("yake-") and b.keyword_links


def test_hf_check_command_reports_without_revealing_the_token(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: make_settings(hf_token=None))
    assert cli.main(["hf-check"]) == 1
    assert "NAHI MILA" in capsys.readouterr().out

    monkeypatch.setattr(cli, "get_settings", lambda: make_settings(hf_token=SECRET, keyword_method="hf"))
    monkeypatch.setattr(HFEmbeddingClient, "embed", lambda self, texts: np.ones((1, 384)))
    assert cli.main(["hf-check"]) == 0
    out = capsys.readouterr().out
    assert "THEEK" in out and "384" in out and SECRET not in out

    def boom(self, texts):
        raise HFError("Hugging Face ne token qabool nahi kiya (HTTP 401)")
    monkeypatch.setattr(HFEmbeddingClient, "embed", boom)
    assert cli.main(["hf-check"]) == 1
    out = capsys.readouterr().out
    assert "NAKAM" in out and SECRET not in out
