"""TenderSource: walks listing pages (with pagination) and enriches each row from its detail page."""
import logging
from dataclasses import replace
from typing import Iterator
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import yaml

from app.config import Settings, get_settings
from app.normalize import RawTender
from app.scraper.client import BlockedError, FetchError, PlaywrightFetcher, SiteClient
from app.scraper.parser import parse_detail, parse_listing

log = logging.getLogger(__name__)


def load_site_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def with_page(url: str, param: str, n: int) -> str:
    u = urlparse(url)
    q = dict(parse_qsl(u.query))
    q[param] = str(n)
    return urlunparse(u._replace(query=urlencode(q)))


class TenderSource:
    def __init__(self, settings: Settings | None = None, client: SiteClient | None = None, site_cfg: dict | None = None):
        self.settings = settings or get_settings()
        self.client = client or SiteClient(self.settings)
        self.cfg = site_cfg or load_site_config(self.settings.site_config_path)
        self.fetcher = PlaywrightFetcher(self.client) if self.cfg.get("render") else self.client
        self.pages_fetched = 0

    def _abs(self, url: str) -> str:
        return urljoin(self.cfg["base_url"], url)

    def iter_tenders(self) -> Iterator[RawTender]:
        """Yields tenders from all listing pages. A failure on a page raises (earlier rows were already yielded)."""
        listing_cfg = self.cfg.get("listing", {})
        detail_cfg = self.cfg.get("detail", {})
        for start in self.cfg["listing_urls"]:
            url, seen, page_no, seen_rows = self._abs(start), set(), 0, set()
            param = listing_cfg.get("page_param")  # e.g. "page" => /viewall?page=2
            while url and url not in seen and page_no < self.settings.max_pages:
                seen.add(url)
                page_no += 1
                html = self.fetcher.get_html(url)
                self.pages_fetched += 1
                rows, next_url = parse_listing(html, url, listing_cfg)
                fresh = [r for r in rows if (r.source_url or id(r)) not in seen_rows]
                log.info("listing page parsed", extra={"ctx": {"url": url, "rows": len(rows), "new_rows": len(fresh)}})
                if not fresh:
                    break  # empty page, or the site repeated a page: stop
                seen_rows.update(r.source_url or id(r) for r in fresh)
                for raw in fresh:
                    yield self._enrich(raw, detail_cfg)
                url = with_page(self._abs(start), param, page_no + 1) if param else next_url

    def _enrich(self, raw: RawTender, detail_cfg: dict) -> RawTender:
        if not detail_cfg.get("enabled", False) or not raw.source_url:
            return raw
        if any(raw.source_url.lower().split("?")[0].endswith(e) for e in (".pdf", ".doc", ".docx")):
            return raw  # source_url is itself a document, there is no detail page
        try:
            html = self.fetcher.get_html(raw.source_url)
            self.pages_fetched += 1
        except BlockedError:
            raise
        except FetchError as e:
            log.warning("detail page failed, keeping listing data", extra={"ctx": {"url": raw.source_url, "error": str(e)}})
            return raw
        d = parse_detail(html, raw.source_url, detail_cfg)
        docs = list(dict.fromkeys(raw.document_urls + d.pop("document_urls", [])))
        merged = {k: v for k, v in d.items() if k in RawTender.__dataclass_fields__ and v}
        # listing values win for identity fields only when detail has none
        base = {k: v for k, v in raw.as_dict().items() if v not in (None, [], {})}
        base.update(merged)
        base["document_urls"] = docs
        return RawTender(**base)

    def download(self, url: str) -> tuple[bytes, str]:
        return self.client.download(url)
