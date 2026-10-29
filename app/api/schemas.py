from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Meta(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int


class ListResponse(BaseModel, Generic[T]):
    data: list[T]
    meta: Meta


class ItemResponse(BaseModel, Generic[T]):
    data: T


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list | dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class CategoryOut(BaseModel):
    name: str
    group: str | None = None
    score: float | None = None
    matched_terms: list[str] | None = None


class KeywordOut(BaseModel):
    term: str
    score: float | None = None
    method: str | None = None
    snippet: str | None = None


class KeywordCount(BaseModel):
    term: str
    tender_count: int


class DocumentOut(BaseModel):
    url: str
    filename: str | None = None
    status: str
    has_text: bool


class TenderSummary(BaseModel):
    id: int
    reference_number: str | None
    title: str
    department: str | None
    source_category: str | None
    categories: list[str]
    keywords: list[str]
    publication_date: date | None
    closing_date: date | None
    status: str
    source_url: str | None
    first_seen_at: datetime
    updated_at: datetime


class TenderDetail(TenderSummary):
    description: str | None
    eligibility: str | None
    documents: list[DocumentOut]
    keyword_details: list[KeywordOut]
    category_details: list[CategoryOut]
    nlp_status: str | None = Field(None, description="pending | done | failed")
    nlp_method: str | None = None
    last_seen_at: datetime


class Health(BaseModel):
    status: str
    database: str
    time: datetime
