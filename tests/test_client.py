import pytest
import requests

from app.scraper.client import BlockedError, FetchError, SiteClient
from tests.conftest import make_settings


class FakeResp:
    def __init__(self, status=200, text="ok", headers=None, content=b""):
        self.status_code, self.text, self.headers, self._c = status, text, headers or {}, content

    def iter_content(self, n):
        yield self._c

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls, self.headers = list(responses), [], {}

    def get(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return FakeResp(404)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(responses, **kw):
    return SiteClient(make_settings(**kw), session=FakeSession(responses), sleep=lambda s: None)


def test_retries_then_succeeds():
    c = client([requests.ConnectionError("boom"), FakeResp(503), FakeResp(200, "<html>fine</html>")], max_retries=3)
    assert "fine" in c.get_html("https://x.pk/a")


def test_gives_up_after_retries():
    c = client([FakeResp(500), FakeResp(500)], max_retries=2)
    with pytest.raises(FetchError):
        c.get_html("https://x.pk/a")


def test_403_is_blocked_not_retried():
    c = client([FakeResp(403)])
    with pytest.raises(BlockedError):
        c.get_html("https://x.pk/a")
    assert c.requests_made == 1


def test_captcha_page_stops_instead_of_bypassing():
    c = client([FakeResp(200, '<div class="g-recaptcha"></div>')])
    with pytest.raises(BlockedError):
        c.get_html("https://x.pk/a")


def test_robots_disallow():
    class S(FakeSession):
        def get(self, url, **kw):
            if url.endswith("/robots.txt"):
                return FakeResp(200, "User-agent: *\nDisallow: /private")
            return super().get(url, **kw)
    c = SiteClient(make_settings(), session=S([FakeResp()]), sleep=lambda s: None)
    with pytest.raises(BlockedError):
        c.get_html("https://x.pk/private/page")
    assert c.get_html("https://x.pk/public") == "ok"


def test_download_size_cap():
    c = client([FakeResp(200, headers={"Content-Length": str(50 * 1024 * 1024)})], max_document_mb=1)
    with pytest.raises(FetchError):
        c.download("https://x.pk/big.pdf")
    c = client([FakeResp(200, headers={"Content-Type": "application/pdf; x=y"}, content=b"abc")])
    assert c.download("https://x.pk/a.pdf") == (b"abc", "application/pdf")
