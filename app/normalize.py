"""Normalisation, validation and identity helpers for scraped tenders."""
import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlparse

from dateutil import parser as dtparser

_WS = re.compile(r"\s+")


class ValidationError(ValueError):
    pass


@dataclass
class RawTender:
    """Whatever the scraper managed to extract, as strings. Nothing is interpreted yet."""
    title: str | None = None
    reference_number: str | None = None
    department: str | None = None
    description: str | None = None
    category: str | None = None
    publication_date: str | None = None
    closing_date: str | None = None
    status: str | None = None
    eligibility: str | None = None
    source_url: str | None = None  # detail page, or main document when no detail page exists
    document_urls: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class NormalizedTender:
    title: str
    reference_number: str | None
    department: str | None
    description: str | None
    category: str | None
    publication_date: date | None
    closing_date: date | None
    status: str
    eligibility: str | None
    source_url: str | None
    document_urls: list[str]


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = _WS.sub(" ", str(value).replace("\xa0", " ")).strip()
    return text or None


def parse_date(value: str | date | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_text(value)
    if not text:
        return None
    text = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    try:
        parsed = dtparser.parse(text, dayfirst=True, fuzzy=True, default=datetime(1900, 1, 1)).date()
    except (ValueError, OverflowError):
        return None
    return parsed if 2000 <= parsed.year <= 2100 else None


def normalize_status(text: str | None, closing: date | None, today: date | None = None) -> str:
    today = today or date.today()
    t = (clean_text(text) or "").lower()
    if "cancel" in t:
        return "cancelled"
    if "award" in t:
        return "awarded"
    if any(w in t for w in ("closed", "expired", "ended")):
        return "closed"
    if any(w in t for w in ("open", "active", "live", "current")):
        return "closed" if closing and closing < today else "open"
    if closing:
        return "closed" if closing < today else "open"
    return "unknown"


def _alnum(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def make_dedupe_key(reference: str | None, department: str | None, title: str) -> str:
    """Reference numbers are only unique per procuring agency, so the key is (reference, department).
    Tenders without a reference fall back to (title, department)."""
    if _alnum(reference):
        basis = f"ref|{_alnum(reference)}|{_alnum(department)}"
    else:
        basis = f"title|{_alnum(title)}|{_alnum(department)}"
    return hashlib.sha256(basis.encode()).hexdigest()


def filename_from_url(url: str) -> str | None:
    name = os.path.basename(urlparse(url).path)
    return name[:500] or None


def normalize_raw(raw: RawTender, today: date | None = None) -> NormalizedTender:
    title = clean_text(raw.title)
    if not title or len(title) < 3:
        raise ValidationError("tender has no usable title")
    closing = parse_date(raw.closing_date)
    pub = parse_date(raw.publication_date)
    if pub and closing and closing < pub:
        # keep both (could be a source typo) but do not guess which is wrong
        pass
    docs: list[str] = []
    for u in raw.document_urls or []:
        u = (u or "").strip()
        if u and u not in docs:
            docs.append(u)
    return NormalizedTender(
        title=title,
        reference_number=clean_text(raw.reference_number),
        department=clean_text(raw.department),
        description=clean_text(raw.description),
        category=clean_text(raw.category),
        publication_date=pub,
        closing_date=closing,
        status=normalize_status(raw.status, closing, today),
        eligibility=clean_text(raw.eligibility),
        source_url=(raw.source_url or "").strip() or None,
        document_urls=docs,
    )


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
