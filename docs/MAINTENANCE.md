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

## DeepSeek API key
* Lives only in `.env` as `DEEPSEEK_API_KEY`. Check it exists without showing it: `grep -c '^DEEPSEEK_API_KEY=.' .env` (prints 1 or 0).
* Test: `docker compose exec worker python -m app.cli llm-check`. `HTTP 402` = balance finished (top up on platform.deepseek.com); `HTTP 401` = wrong key.
* While the LLM is unavailable keywords/categories still work (taxonomy + YAKE) and the tender records which method was used.
* Rotate: create a new key on the DeepSeek platform, replace the line in `.env`, `docker compose restart api worker`, delete the old key.
* If a key was ever pasted into chat, email or Git, delete it and make a new one.

## After changing `.env`
`docker compose restart` does NOT re-read `.env`. Use `docker compose up -d --force-recreate --no-build api worker` (add `--build` if the code changed).
Test the DeepSeek key straight from the file without printing it:
`curl -s -o /dev/null -w "HTTP %{http_code}\n" https://api.deepseek.com/models -H "Authorization: Bearer $(grep '^DEEPSEEK_API_KEY=' .env | cut -d= -f2-)"` (200 = valid, 401 = wrong key).
