# API Integration Guide

Base URL: `http://<host>:8000/api/v1` · Interactive docs (Swagger UI): `http://<host>:8000/docs` · OpenAPI JSON: `/openapi.json`

## Authentication
Send your key in the `X-API-Key` header on every request except `/health`. Missing/invalid key → `401`; admin-only endpoint with a normal key → `403`.
Default limit: 120 requests/minute per key → `429` with a `Retry-After` header.

## Response format
* List: `{"data": [...], "meta": {"page", "page_size", "total", "pages"}}`
* Single item: `{"data": {...}}`
* Error: `{"error": {"code", "message", "details"}}` (codes: `unauthorized`, `forbidden`, `not_found`, `validation_error`, `rate_limited`, `internal_error`)

## Endpoints
| Method & path | Purpose |
|---|---|
| `GET /tenders` | List with pagination, filters, sorting |
| `GET /tenders/search?q=` | Text search (every word must match title, description, reference, department or keyword) |
| `GET /tenders/{id}` | Full details: description, eligibility, documents, keywords with snippets, categories |
| `GET /tenders/{id}/keywords` | Keywords for one tender (score, method, supporting snippet) |
| `GET /keywords?q=` | All keywords with tender counts |
| `GET /categories` | Taxonomy categories with tender counts |
| `GET /health` | Liveness + DB check (no key needed) |
| `GET /admin/status`, `/admin/runs`, `/admin/failures`, `POST /admin/run`, `POST /admin/tenders/{id}/reprocess` | Admin key only |

### `/tenders` query parameters
`q`, `keyword` (exact), `category` (category name **or group**, e.g. `Information Technology`), `department` (substring),
`status` (`open|closed|cancelled|awarded|unknown`; `open` excludes tenders past their closing date),
`published_after/before`, `closing_after/before` (`YYYY-MM-DD`), `sort` (`published|closing|first_seen|title`, default `first_seen`),
`order` (`asc|desc`), `page` (≥1), `page_size` (1–100, default 20).

## Examples
```bash
export KEY=your-api-key; export H=http://localhost:8000/api/v1
curl -H "X-API-Key: $KEY" "$H/tenders?keyword=software&status=open&sort=closing&order=asc"
curl -H "X-API-Key: $KEY" "$H/tenders?category=Information%20Technology&page_size=50"
curl -H "X-API-Key: $KEY" "$H/tenders/search?q=road%20repair"
curl -H "X-API-Key: $KEY" "$H/tenders/42"
curl -H "X-API-Key: $KEY" "$H/tenders/42/keywords"
```
```python
import requests
r = requests.get("http://localhost:8000/api/v1/tenders", headers={"X-API-Key": KEY},
                 params={"category": "Information Technology", "status": "open", "page_size": 100})
r.raise_for_status()
for t in r.json()["data"]:
    print(t["id"], t["title"], t["closing_date"], t["keywords"])
```
**Syncing new tenders:** poll `GET /tenders?sort=first_seen&order=desc` every few minutes and stop when you reach an `id` you already have; use `updated_at` to detect edits.

### `GET /tenders?category=Information%20Technology&page_size=1`
```json
{
  "data": [
    {
      "id": 2,
      "reference_number": "IT-77",
      "title": "Procurement of 40 laptops and 10 network printers",
      "department": "Education Department GB",
      "source_category": null,
      "categories": [
        "IT Equipment and Computer Hardware",
        "Networking and Cybersecurity"
      ],
      "keywords": [
        "network printers",
        "procurement",
        "laptops",
        "network",
        "printers"
      ],
      "publication_date": "2026-01-01",
      "closing_date": "2099-01-01",
      "status": "open",
      "source_url": "https://x.pk/t/IT-77",
      "first_seen_at": "2026-10-21T08:18:29.100536",
      "updated_at": "2026-10-21T08:18:29.102138"
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 1,
    "total": 1,
    "pages": 1
  }
}
```
### `GET /tenders/1`
```json
{
  "data": {
    "id": 1,
    "reference_number": "AB/Cash/Notice/522",
    "title": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work)",
    "department": "Executive Engineer B&R Division C&WD Gilgit",
    "source_category": null,
    "categories": [
      "Construction and Road Maintenance"
    ],
    "keywords": [
      "road",
      "repair of link road",
      "road jutial gilgit",
      "jutial gilgit",
      "radio pakistan",
      "pakistan to fcna",
      "road jutial",
      "metaling work",
      "pec licensed",
      "link road",
      "radio",
      "fcna",
      "jutial",
      "road at radio",
      "fcna hq road",
      "metaling",
      "repair of link"
    ],
    "publication_date": "2026-05-10",
    "closing_date": "2099-12-30",
    "status": "open",
    "source_url": "https://ppra.gbit.gov.pk/tender/522",
    "first_seen_at": "2026-10-21T08:18:29.067454",
    "updated_at": "2026-10-21T08:18:29.097616",
    "description": "Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.",
    "eligibility": "PEC licensed, category C-4 and above",
    "documents": [
      {
        "url": "https://ppra.gbit.gov.pk/doc/522.pdf",
        "filename": "522.pdf",
        "status": "pending",
        "has_text": false
      }
    ],
    "keyword_details": [
      {
        "term": "road",
        "score": 1.0,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work…"
      },
      {
        "term": "repair of link road",
        "score": 1.0,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work…"
      },
      {
        "term": "road jutial gilgit",
        "score": 0.9698,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.\nPEC licensed, category C-4 an…"
      },
      {
        "term": "jutial gilgit",
        "score": 0.936,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.\nPEC licensed, category C-4 an…"
      },
      {
        "term": "radio pakistan",
        "score": 0.917,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.…"
      },
      {
        "term": "pakistan to fcna",
        "score": 0.917,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.\nPEC lic…"
      },
      {
        "term": "road jutial",
        "score": 0.7039,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.\nPEC licensed, category…"
      },
      {
        "term": "metaling work",
        "score": 0.6836,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.\nPEC licensed, category C-4 and above"
      },
      {
        "term": "pec licensed",
        "score": 0.545,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.\nPEC licensed, category C-4 and above"
      },
      {
        "term": "link road",
        "score": 0.5314,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work…"
      },
      {
        "term": "radio",
        "score": 0.5136,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the l…"
      },
      {
        "term": "fcna",
        "score": 0.5136,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.\nPEC lic…"
      },
      {
        "term": "jutial",
        "score": 0.5136,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.\nPEC licensed, category…"
      },
      {
        "term": "road at radio",
        "score": 0.4769,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the l…"
      },
      {
        "term": "fcna hq road",
        "score": 0.4769,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.\nPEC licensed, c…"
      },
      {
        "term": "metaling",
        "score": 0.4537,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling work on the link road.\nPEC licensed, category C-4 and above"
      },
      {
        "term": "repair of link",
        "score": 0.4315,
        "method": "yake-0.7.1+taxonomy-2026.1",
        "snippet": "Repair of link road at Radio Pakistan to FCNA HQ road Jutial Gilgit (Metaling work). Sealed tenders are invited from PEC licensed contractors for metaling…"
      }
    ],
    "category_details": [
      {
        "name": "Construction and Road Maintenance",
        "group": "Works",
        "score": 1.0,
        "matched_terms": [
          "road",
          "repair of link road",
          "metaling"
        ]
      }
    ],
    "nlp_status": "done",
    "nlp_method": "yake-0.7.1+taxonomy-2026.1",
    "last_seen_at": "2026-10-21T08:18:29.067454"
  }
}
```
### `GET /keywords?page_size=3`
```json
{
  "data": [
    {
      "term": "fcna",
      "tender_count": 1
    },
    {
      "term": "fcna hq road",
      "tender_count": 1
    },
    {
      "term": "jutial",
      "tender_count": 1
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 3,
    "total": 22,
    "pages": 8
  }
}
```
### Error example — `GET /tenders?page_size=500`
```json
{
  "error": {
    "code": "validation_error",
    "message": "Invalid request parameters",
    "details": [
      {
        "field": "page_size",
        "message": "Input should be less than or equal to 100"
      }
    ]
  }
}
```
*(Examples are generated from the running app on synthetic data.)*

## Field notes
* `status` is computed as of today, so a tender past its closing date reads `closed` even before the next scrape.
* `categories` / `keywords` are empty until the tender has been processed (`nlp_status` in the detail view: `pending|done|failed`).
* `source_url` points back to the original GB-PPRA page or document. Original text is never altered; AI output is stored separately and carries its `method` (extractor version + taxonomy version).
