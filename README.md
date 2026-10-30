# GB-PPRA Tender Monitor

Scrapes tenders from GB-PPRA, detects new/changed tenders automatically, extracts keywords and categories with NLP,
stores everything in PostgreSQL and serves it through a FastAPI REST API.

```
 scheduler (APScheduler, worker container)
     │ every SCRAPE_INTERVAL_MINUTES
     ▼
 TenderSource ──► validate/normalise ──► upsert (dedupe key) ──► documents → text ──► NLP (keywords + categories)
 (listing pages,      (app/normalize)      new | updated | unchanged   (PyMuPDF)        (YAKE + taxonomy)
  pagination, detail)                      + tender_changes audit                            │
                                                                                            ▼
                                  scraping_logs ◄── run summary                      PostgreSQL ──► FastAPI (/api/v1, Swagger at /docs)
```

Two long-running processes share one database: **worker** (scheduler) and **api**. The API never scrapes; it only reads
the database. A manual run is `python -m app.cli scrape` or `POST /api/v1/admin/run`; a DB-level guard stops two runs
overlapping and abandons a run that died mid-way, so the next scheduled run always proceeds.

## ⚠️ Step 0 — calibrate the scraper (required, ~15 minutes)

This code was built **without being able to load the live GB-PPRA pages** (the build environment had no access to
`ppra.gbit.gov.pk`). **The real site is `https://www.gbppra.gov.pk`** (tenders at `/viewall?page=N`, one tender per `/procurementdetails/<id>`); `config/site.yaml` is pre-set for it, but the HTML was never inspected here, so verify with `probe` first. From a machine that can reach the site:

```bash
python -m app.cli probe https://ppra.gbit.gov.pk/<page-that-lists-tenders>    # shows rows the parser finds
python -m app.cli probe --file saved_page.html                                 # same, from a saved page
```
* Rows look right → copy that URL into `listing_urls` in `config/site.yaml`.
* No/garbled rows → switch `listing.mode` to `css` and set `row`, `fields`, `next_page` (examples are in the file).
* Tender data on a detail page → `detail.enabled: true`.
* Listing is built by JavaScript → `render: true` and install Playwright (`requirements-optional.txt`).
* Notices are only PDFs (the main PPRA site publishes PDFs under `/doc/...`) → the PDF becomes the tender's document;
  fields not present in the listing (e.g. reference number) are left empty, and tenders are then de-duplicated by title + department.

The scraper honours `robots.txt`, waits `REQUEST_DELAY_SECONDS` between requests, retries temporary errors with
back-off, and **stops** (never works around) on 401/403 or CAPTCHA pages. Check the site's terms and consider asking
GB-PPRA for a data feed or permission.

## Quick start (Docker)

```bash
cp .env.example .env        # set passwords + API keys
docker compose up -d --build
open http://localhost:8000/docs
curl -H "X-API-Key: <API_KEYS value>" http://localhost:8000/api/v1/tenders
```
`migrate` applies Alembic migrations, `worker` starts scraping immediately (`RUN_ON_START=true`) then every
`SCRAPE_INTERVAL_MINUTES`.

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                   # 100 tests, no network or Postgres needed (SQLite in-memory)
DATABASE_URL=postgresql+psycopg2://... alembic upgrade head
uvicorn app.asgi:app --reload
python -m app.scheduler                  # or: python -m app.cli scrape
```

## Project layout

```
app/config.py            settings from env          app/monitor.py     one run: scrape→store→docs→NLP→log
app/models.py            SQLAlchemy schema          app/scheduler.py   APScheduler worker
app/normalize.py         dates, status, dedupe key  app/ingest.py       upsert + change detection
app/scraper/             client, parser, source, documents
app/nlp/                 extractors (YAKE/TF-IDF/KeyBERT), taxonomy, pipeline
app/api/                 FastAPI app, routes, admin, security, queries
alembic/                 migrations                 config/            site.yaml, taxonomy.yaml
tests/                   pytest suite               scripts/evaluate_keywords.py
docs/INTEGRATION.md      API guide for the integrating developer    docs/MAINTENANCE.md   operations
```

## Design decisions

* **Simplest architecture that meets the brief:** APScheduler in a worker container rather than Celery/Redis; one
  Postgres database; synchronous SQLAlchemy. Move to Celery only if you need many concurrent scrape jobs.
* **Duplicate prevention:** unique `dedupe_key` = hash(normalised reference number, normalised department); falls back
  to (title, department) when there is no reference. Reference numbers are only unique per agency, hence the department.
* **Change detection:** title, description, closing/publication date, status, eligibility, category and document links
  are compared on every run; each change is written to `tender_changes`. A sparse page never overwrites stored data with blanks.
* **Reprocessing:** `tenders.content_hash` covers everything the NLP reads (title, description, eligibility, document
  text). If it differs from `processing_status.nlp_content_hash`, keywords are recomputed. Failed tenders are retried up
  to `max_nlp_attempts`. `python -m app.cli reprocess --all` forces a re-run (e.g. after editing the taxonomy).
* **Keywords:** YAKE (fast, no model download) by default + curated taxonomy terms; each result stores score, snippet and
  method/version string. TF-IDF and KeyBERT are available via `KEYWORD_METHOD`.
* **Idempotent & retry-safe:** every tender is processed in a SAVEPOINT; failures are logged in `scraping_logs.details`
  and never stop the batch; re-running a half-finished run is harmless.
* **Not included:** OCR is optional (`OCR_ENABLED=true` needs the Tesseract binary); legacy `.doc` files are not text-extracted;
  rate limiting is in-process per API worker (use Redis for a shared limit if you scale API replicas).
