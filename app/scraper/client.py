"""Polite HTTP client: robots.txt, request spacing, timeouts, retry with backoff, no CAPTCHA bypass."""
import logging
import time
from urllib import robotparser
from urllib.parse import urlparse

import requests

from app.config import Settings
from app.scraper.parser import looks_like_captcha

log = logging.getLogger(__name__)


class FetchError(Exception):
    """Temporary or unexpected fetch failure (already retried)."""


class BlockedError(Exception):
    """The site refuses access (robots.txt, 401/403, CAPTCHA). We stop instead of working around it."""


class SiteClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None, sleep=time.sleep):
        self.s = settings
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent, "Accept-Language": "en"})
        self._sleep = sleep
        self._last = 0.0
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self.requests_made = 0

    # -- politeness -----------------------------------------------------------
    def _wait(self):
        delta = time.monotonic() - self._last
        if self._last and delta < self.s.request_delay_seconds:
            self._sleep(self.s.request_delay_seconds - delta)
        self._last = time.monotonic()

    def allowed(self, url: str) -> bool:
        if not self.s.respect_robots:
            return True
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                resp = self.session.get(f"{origin}/robots.txt", timeout=self.s.request_timeout_seconds)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                    self._robots[origin] = rp
                else:
                    self._robots[origin] = None  # no robots.txt => allowed
            except requests.RequestException:
                self._robots[origin] = None
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(self.s.user_agent, url)

    # -- fetching -------------------------------------------------------------
    def _request(self, url: str, stream: bool = False) -> requests.Response:
        if not self.allowed(url):
            raise BlockedError(f"robots.txt disallows {url}")
        last_exc: Exception | None = None
        for attempt in range(1, self.s.max_retries + 1):
            self._wait()
            try:
                self.requests_made += 1
                resp = self.session.get(url, timeout=self.s.request_timeout_seconds, stream=stream)
            except requests.RequestException as e:
                last_exc = e
            else:
                if resp.status_code in (401, 403):
                    raise BlockedError(f"{resp.status_code} for {url}")
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = FetchError(f"HTTP {resp.status_code} for {url}")
                    retry_after = resp.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        self._sleep(min(int(retry_after), 60))
                elif resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code} for {url}")
                else:
                    return resp
            if attempt < self.s.max_retries:
                self._sleep(min(2 ** attempt, 30))
        raise FetchError(f"giving up on {url}: {last_exc}")

    def get_html(self, url: str) -> str:
        html = self._request(url).text
        if looks_like_captcha(html):
            raise BlockedError(f"CAPTCHA/challenge page at {url}")
        return html

    def download(self, url: str) -> tuple[bytes, str]:
        """Download a document with a hard size cap. Returns (bytes, content_type)."""
        limit = self.s.max_document_mb * 1024 * 1024
        resp = self._request(url, stream=True)
        try:
            declared = int(resp.headers.get("Content-Length", 0) or 0)
            if declared > limit:
                raise FetchError(f"document too large ({declared} bytes)")
            chunks, size = [], 0
            for chunk in resp.iter_content(65536):
                size += len(chunk)
                if size > limit:
                    raise FetchError("document exceeds size limit")
                chunks.append(chunk)
            return b"".join(chunks), resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        finally:
            resp.close()


class PlaywrightFetcher:
    """Optional: for JavaScript-rendered listing pages (site.yaml: render: true).
    Requires `pip install playwright && playwright install chromium`."""

    def __init__(self, client: SiteClient):
        self.client = client

    def get_html(self, url: str) -> str:
        if not self.client.allowed(url):
            raise BlockedError(f"robots.txt disallows {url}")
        from playwright.sync_api import sync_playwright  # imported lazily on purpose
        self.client._wait()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=self.client.s.user_agent)
                page.goto(url, wait_until="networkidle", timeout=self.client.s.request_timeout_seconds * 1000)
                html = page.content()
            finally:
                browser.close()
        if looks_like_captcha(html):
            raise BlockedError(f"CAPTCHA/challenge page at {url}")
        return html
