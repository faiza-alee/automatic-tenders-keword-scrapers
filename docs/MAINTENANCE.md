# Operations & Maintenance

## Logging
JSON lines on stdout (`docker compose logs -f worker api`). Fields: `ts, level, logger, msg` + context (`url`, `tender_id`, `status`, `ms`…).
Set `LOG_LEVEL=DEBUG` for more detail. Every API request is logged with status and latency.

## Where to look when something is wrong
| Question | Where |
|---|---|
| Did the last run work? | `GET /api/v1/admin/status` (last run + last successful run), `GET /admin/runs` |
| Which records failed and why? | `scraping_logs.details.failed_records` (also in `/admin/runs`); `GET /admin/failures` for NLP failures |
| No new tenders for days | `last_successful_run`; check `error` for `BlockedError` (site blocked us / CAPTCHA / robots) or a parser returning 0 rows (site layout changed → re-run `app.cli probe`) |
| What changed on a tender? | table `tender_changes` (field, old, new, time) |
| Run stuck as "running" | automatically marked `abandoned` after `RUN_TIMEOUT_MINUTES` |

Run statuses: `success` (no errors) · `partial` (some tenders/pages/NLP failed, rest saved) · `failed` (nothing processed) · `skipped` (another run active) · `abandoned`.

## Routine tasks
* **Change interval:** edit `SCRAPE_INTERVAL_MINUTES` in `.env`, `docker compose up -d worker`.
* **Manual run:** `docker compose exec worker python -m app.cli scrape` or `POST /api/v1/admin/run` (admin key).
* **Add categories / terms:** edit `config/taxonomy.yaml`, bump `version`, then `python -m app.cli reprocess --all`.
* **Switch keyword method:** set `KEYWORD_METHOD`, then `reprocess --all`. Compare methods first with `python scripts/evaluate_keywords.py your_labelled_samples.json`.
* **Rotate API keys:** edit `API_KEYS` (comma-separated; keep old + new during the hand-over), restart `api`.
* **Schema change:** edit `app/models.py`, `alembic revision --autogenerate -m "..."`, review, `alembic upgrade head`.
* **Backups:** `docker compose exec db pg_dump -U $POSTGRES_USER $POSTGRES_DB > backup.sql` (the scraped data can be rebuilt from the site, but tender history and change logs cannot).
* **Site layout changed:** `python -m app.cli probe <url>`; adjust `config/site.yaml`. The parser needs no code change for selector edits.

## Scaling notes
Search uses `ILIKE`; for >100k tenders add a `pg_trgm` GIN index on `tenders.title`/`description` or move to PostgreSQL full-text search.
For multiple API replicas, replace the in-process rate limiter with a Redis-backed one. Only run **one** worker container.

## Compliance
Respect GB-PPRA's terms of use; keep `RESPECT_ROBOTS=true`; keep a real contact address in `USER_AGENT`; never add CAPTCHA/login bypasses.
