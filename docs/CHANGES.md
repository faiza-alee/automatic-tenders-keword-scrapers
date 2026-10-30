# Changes since the first version (all included in this zip)

1. **Scraper retargeted** to the real site `https://www.gbppra.gov.pk` (`/viewall?page=N` + `/procurementdetails/<id>`);
   new `links` mode, `page_param` pagination, label/value detail parsing, 3-cell rows ("In Objection"), title fallbacks.
2. **Bug fixes:** NumPy scores crashed PostgreSQL inserts; "opening date" mistaken for closing date; site-wide meta text
   stored as description.
3. **Keywords:** boilerplate and office-letterhead phrases removed; title phrases rank first; taxonomy abbreviations
   (`const`, `mw`) no longer become keywords.
4. **Taxonomy 2026.4:** 21 categories built from real tenders (added Finance and Investment; Printing, Media and Publicity;
   Machinery and Heavy Equipment; Emergency and Security; General Supplies and Mixed Items).
5. **Dashboard** at `/dashboard` (status, runs, tenders, auto-refresh); `/` redirects to it.
6. **Taxonomy 2026.6:** 22 categories; added "Results and Notices" (evaluation reports, corrigenda, cancellations) so non-tender entries can be excluded.
7. **Docker:** `pymupdf<2` and pip retries so builds survive slow connections.
