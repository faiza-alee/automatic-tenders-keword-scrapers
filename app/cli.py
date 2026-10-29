"""Command line tools.

  python -m app.cli scrape                # run one monitoring cycle now (manual trigger)
  python -m app.cli nlp                   # process tenders that are pending / changed / failed
  python -m app.cli reprocess --all       # re-run keywords for every tender (e.g. after editing the taxonomy)
  python -m app.cli reprocess --id 42
  python -m app.cli probe URL [--file page.html]   # show what the parser extracts from a page (calibration)
  python -m app.cli sync-taxonomy
"""
import argparse
import json
import sys

from app.config import get_settings
from app.logging_config import setup_logging


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scrape")
    sub.add_parser("nlp")
    sub.add_parser("sync-taxonomy")
    rp = sub.add_parser("reprocess")
    rp.add_argument("--all", action="store_true")
    rp.add_argument("--id", type=int)
    pr = sub.add_parser("probe")
    pr.add_argument("url", nargs="?")
    pr.add_argument("--file", help="parse a saved HTML file instead of fetching")
    pr.add_argument("--detail", action="store_true", help="parse as a detail page")
    a = ap.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    if a.cmd == "probe":
        from app.scraper.client import SiteClient
        from app.scraper.parser import parse_detail, parse_listing
        from app.scraper.source import load_site_config
        cfg = load_site_config(settings.site_config_path)
        base = a.url or cfg["base_url"]
        html = open(a.file, encoding="utf-8").read() if a.file else SiteClient(settings).get_html(base)
        if a.detail:
            print(json.dumps(parse_detail(html, base, cfg.get("detail", {})), indent=2, ensure_ascii=False))
        else:
            rows, nxt = parse_listing(html, base, cfg.get("listing", {}))
            print(f"{len(rows)} rows parsed; next page: {nxt}")
            for r in rows[:10]:
                print(json.dumps({k: v for k, v in r.as_dict().items() if v}, ensure_ascii=False))
            if not rows:
                print("No rows. Set listing.mode to css and configure row/fields in config/site.yaml.")
        return 0

    from app.db import get_session_factory
    from app.nlp.pipeline import mark_for_reprocess, run_pending_nlp, sync_taxonomy
    from app.nlp.taxonomy import Taxonomy

    if a.cmd == "scrape":
        from app.monitor import run_once
        print(json.dumps(run_once(trigger="manual"), indent=2, default=str))
        return 0
    with get_session_factory()() as session:
        if a.cmd == "sync-taxonomy":
            sync_taxonomy(session, Taxonomy.from_yaml(settings.taxonomy_path))
            session.commit()
        elif a.cmd == "reprocess":
            if not a.all and a.id is None:
                print("give --all or --id", file=sys.stderr)
                return 2
            n = mark_for_reprocess(session, None if a.all else a.id)
            session.commit()
            print(f"marked {n} tender(s) for reprocessing")
            print(run_pending_nlp(session, settings=settings))
        elif a.cmd == "nlp":
            print(run_pending_nlp(session, settings=settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
