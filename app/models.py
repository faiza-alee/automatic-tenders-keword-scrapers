"""SQLAlchemy models. Types are kept portable so the same schema runs on PostgreSQL and SQLite (tests)."""
from datetime import date, datetime, timezone

from sqlalchemy import (JSON, Date, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(500), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Category(Base):
    """Taxonomy category (assigned by the AI module). `group_name` allows coarse filtering, e.g. 'Information Technology'."""
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    group_name: Mapped[str | None] = mapped_column(String(200), index=True)
    taxonomy_version: Mapped[str | None] = mapped_column(String(50))


class Keyword(Base):
    __tablename__ = "keywords"
    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tender(Base):
    __tablename__ = "tenders"
    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    reference_number: Mapped[str | None] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), index=True)
    source_category: Mapped[str | None] = mapped_column(String(255))
    publication_date: Mapped[date | None] = mapped_column(Date, index=True)
    closing_date: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    eligibility: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict | None] = mapped_column(JSON)  # original scraped values, untouched
    content_hash: Mapped[str | None] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    department: Mapped[Department | None] = relationship()
    documents: Mapped[list["TenderDocument"]] = relationship(back_populates="tender", cascade="all, delete-orphan")
    keyword_links: Mapped[list["TenderKeyword"]] = relationship(back_populates="tender", cascade="all, delete-orphan")
    category_links: Mapped[list["TenderCategory"]] = relationship(back_populates="tender", cascade="all, delete-orphan")
    changes: Mapped[list["TenderChange"]] = relationship(back_populates="tender", cascade="all, delete-orphan")
    processing: Mapped["ProcessingStatus | None"] = relationship(back_populates="tender", cascade="all, delete-orphan", uselist=False)

    @property
    def effective_status(self) -> str:
        """Status as of *today*, so a tender that passed its closing date reads 'closed' even before the next scrape."""
        if self.status in ("open", "unknown") and self.closing_date and self.closing_date < date.today():
            return "closed"
        return self.status


class TenderDocument(Base):
    __tablename__ = "tender_documents"
    __table_args__ = (UniqueConstraint("tender_id", "url"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    filename: Mapped[str | None] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|ok|no_text|skipped|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str | None] = mapped_column(String(64))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tender: Mapped[Tender] = relationship(back_populates="documents")


class TenderKeyword(Base):
    __tablename__ = "tender_keywords"
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True)
    keyword_id: Mapped[int] = mapped_column(ForeignKey("keywords.id"), primary_key=True, index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    method: Mapped[str] = mapped_column(String(100))  # extraction method + version, for traceability
    snippet: Mapped[str | None] = mapped_column(Text)
    tender: Mapped[Tender] = relationship(back_populates="keyword_links")
    keyword: Mapped[Keyword] = relationship()


class TenderCategory(Base):
    __tablename__ = "tender_categories"
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), primary_key=True, index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    method: Mapped[str] = mapped_column(String(100))
    matched_terms: Mapped[list | None] = mapped_column(JSON)
    tender: Mapped[Tender] = relationship(back_populates="category_links")
    category: Mapped[Category] = relationship()


class TenderChange(Base):
    """Audit trail of detected changes to existing tenders."""
    __tablename__ = "tender_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    tender: Mapped[Tender] = relationship(back_populates="changes")


class ProcessingStatus(Base):
    __tablename__ = "processing_status"
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True)
    nlp_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|done|failed
    nlp_content_hash: Mapped[str | None] = mapped_column(String(64))
    nlp_method: Mapped[str | None] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    tender: Mapped[Tender] = relationship(back_populates="processing")


class ScrapingLog(Base):
    __tablename__ = "scraping_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))  # scheduled | manual
    status: Mapped[str] = mapped_column(String(16), index=True)  # running|success|partial|failed|abandoned
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    found_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    nlp_processed: Mapped[int] = mapped_column(Integer, default=0)
    nlp_failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON)  # e.g. list of failed records

