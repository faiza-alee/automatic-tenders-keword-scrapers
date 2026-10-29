"""HTML parsing for listing and detail pages.

Two modes (set in config/site.yaml):
  auto - find the main table and map columns by header text (works for many table-based listings)
  css  - explicit CSS selectors per field (use this once you've inspected the real site)
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.normalize import RawTender, clean_text

DOC_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rtf", ".txt")

# Order matters: first match wins.
HEADER_RULES = [
    ("ignore", ("s.no", "s. no", "sr", "serial", "#", "action", "view", "download")),
    ("closing_date", ("closing", "last date", "due", "deadline", "submission", "expiry", "expire")),
    ("publication_date", ("publication", "published", "publish", "posted", "advertis", "uploaded", "issue", "opening", "date")),
    ("status", ("status", "state")),
    ("department", ("department", "organization", "organisation", "agency", "procuring", "office", "authority", "ministry")),
    ("category", ("category", "type", "nature", "sector")),
    ("reference_number", ("reference", "ref", "tender no", "tender number", "tender #", "number", "id")),
    ("title", ("title", "subject", "description", "work", "name", "item", "detail", "tender")),
]


def classify_header(text: str) -> str | None:
    t = (clean_text(text) or "").lower()
    if not t:
        return None
    for fieldname, needles in HEADER_RULES:
        if any(n in t for n in needles):
            return None if fieldname == "ignore" else fieldname
    return None


def is_doc_link(href: str) -> bool:
    h = href.lower()
    return h.split("?")[0].endswith(DOC_EXTS) or "proc_files" in h


def _links(node: Tag, base_url: str) -> tuple[str | None, list[str]]:
    detail, docs = None, []
    for a in node.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urljoin(base_url, href)
        if is_doc_link(url):
            if url not in docs:
                docs.append(url)
        elif detail is None:
            detail = url
    return detail, docs


def _select_value(node: Tag, spec: str) -> str | None:
    """'css selector' or 'css selector@attr'."""
    if not spec:
        return None
    sel, _, attr = spec.partition("@")
    el = node.select_one(sel.strip()) if sel.strip() else node
    if el is None:
        return None
    return clean_text(el.get(attr.strip()) if attr else el.get_text(" "))


def _next_url(soup: BeautifulSoup, base_url: str, cfg: dict) -> str | None:
    sel = cfg.get("next_page")
    if sel:
        el = soup.select_one(sel)
        return urljoin(base_url, el["href"]) if el and el.get("href") else None
    for a in soup.find_all("a", href=True):  # auto: rel=next or text 'next' / '>' / '»'
        label = (clean_text(a.get_text(" ")) or "").lower()
        if "next" in (a.get("rel") or []) or label in ("next", "next »", "next >", ">", "»", "›"):
            return urljoin(base_url, a["href"])
    return None


def parse_listing(html: str, base_url: str, cfg: dict) -> tuple[list[RawTender], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    mode = cfg.get("mode", "auto")
    if mode == "css":
        rows = _parse_css(soup, base_url, cfg)
    elif mode == "links":
        rows = _parse_links_mode(soup, base_url, cfg)
    else:
        rows = _parse_auto(soup, base_url, cfg)
    return rows, _next_url(soup, base_url, cfg)


GENERIC_LINK_TEXT = {"view details", "details", "view", "more", "read more", "download", "notice", "document"}


def _parse_links_mode(soup: BeautifulSoup, base_url: str, cfg: dict) -> list[RawTender]:
    """Layout-independent: every link to a tender detail page is one tender; the detail page supplies the data."""
    pattern = cfg.get("link_pattern", "procurementdetails")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if pattern not in href:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        label = clean_text(a.get_text(" ")) or ""
        title = None if (len(label) < 3 or label.lower() in GENERIC_LINK_TEXT) else label
        out.append(RawTender(title=title, source_url=url))
    return out


def _parse_css(soup: BeautifulSoup, base_url: str, cfg: dict) -> list[RawTender]:
    fields = cfg.get("fields", {})
    out = []
    for row in soup.select(cfg["row"]):
        values = {k: _select_value(row, v) for k, v in fields.items()}
        detail, docs = _links(row, base_url)
        values_url = values.pop("source_url", None)
        out.append(RawTender(
            **{k: v for k, v in values.items() if k in RawTender.__dataclass_fields__},
            source_url=urljoin(base_url, values_url) if values_url else (detail or (docs[0] if docs else None)),
            document_urls=docs,
        ))
    return out


def _parse_auto(soup: BeautifulSoup, base_url: str, cfg: dict) -> list[RawTender]:
    best: list[RawTender] = []
    for table in soup.find_all("table"):
        header_cells = table.find("tr")
        if header_cells is None:
            continue
        headers = [classify_header(c.get_text(" ")) for c in header_cells.find_all(["th", "td"])]
        if "title" not in headers:
            continue
        rows = []
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            values: dict = {}
            for fieldname, cell in zip(headers, cells):
                if fieldname and fieldname not in values:
                    values[fieldname] = clean_text(cell.get_text(" "))
            detail, docs = _links(tr, base_url)
            rows.append(RawTender(**values, source_url=detail or (docs[0] if docs else None), document_urls=docs))
        if len(rows) > len(best):
            best = rows
    return best


def parse_detail(html: str, base_url: str, cfg: dict) -> dict:
    """Returns a dict of RawTender field overrides (only non-empty values) plus 'document_urls'."""
    soup = BeautifulSoup(html, "html.parser")
    out: dict = {}
    fields = (cfg or {}).get("fields", {})
    for name, spec in fields.items():
        val = _select_value(soup, spec)
        if val:
            out[name] = val
    if (cfg or {}).get("mode", "auto") == "auto":
        pairs = []  # label/value pairs from 2-column table rows and definition lists
        for tr in soup.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) >= 2:  # extra cells (e.g. an "In Objection" badge) are ignored
                pairs.append((cells[0].get_text(" "), cells[1].get_text(" ")))
        for strong in soup.find_all(["strong", "b"]):
            label = (clean_text(strong.get_text(" ")) or "").rstrip(":").strip()
            parent = strong.parent
            if not label or len(label) > 40 or parent is None or parent.name not in ("li", "p", "div", "span", "td"):
                continue
            value = clean_text(parent.get_text(" ").replace(strong.get_text(" "), "", 1))
            if value:
                pairs.append((label, value.lstrip(": ").strip()))
        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                pairs.append((dt.get_text(" "), dd.get_text(" ")))
        for label, value in pairs:
            low = (clean_text(label) or "").lower()
            if any(w in low for w in ("search engine", "reference", "tender no", "tender number")):
                f = "reference_number"
            elif any(w in low for w in ("eligib", "qualification")):
                f = "eligibility"
            elif any(w in low for w in ("description", "scope", "detail")):
                f = "description"
            else:
                f = classify_header(label)
            if f and clean_text(value) and clean_text(value).lower() not in ("download", "view", "-") and f not in out:
                out[f] = clean_text(value)
        if "title" not in out:  # fallbacks: the <h2> under the "[TSE-...]" heading, then the <title> tag's last part
            h1 = soup.find("h1")
            h2 = h1.find_next("h2") if h1 else None
            heading = clean_text(h2.get_text(" ")) if h2 else None
            page_title = clean_text(soup.title.get_text(" ")) if soup.title else None
            from_tag = clean_text(page_title.split("|")[-1]) if page_title and "|" in page_title else None
            for cand in (heading, from_tag):
                if cand and len(cand) >= 3 and cand.lower() not in ("tender details",):
                    out["title"] = cand
                    break
    _, docs = _links(soup, base_url)
    out["document_urls"] = docs
    return out


CAPTCHA_MARKERS = re.compile(r"g-recaptcha|h-captcha|cf-challenge|cf-turnstile|captcha-container|verify you are human", re.I)


def looks_like_captcha(html: str) -> bool:
    return bool(CAPTCHA_MARKERS.search(html or ""))
