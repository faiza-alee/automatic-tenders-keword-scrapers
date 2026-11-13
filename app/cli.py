
"""Command line tools.

  python -m app.cli scrape
  python -m app.cli nlp
  python -m app.cli extract-pending
  python -m app.cli extract-no-text
  python -m app.cli reprocess --all
  python -m app.cli reprocess --id 42
  python -m app.cli probe URL [--file page.html]
  python -m app.cli sync-taxonomy
  python -m app.cli llm-check
  python -m app.cli llm-compare --n 30
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
    sub.add_parser("extract-pending")
    sub.add_parser("extract-no-text")
    sub.add_parser("sync-taxonomy")
    sub.add_parser("llm-check")

    lc = sub.add_parser("llm-compare")
    lc.add_argument("--n", type=int, default=30)

    rp = sub.add_parser("reprocess")
    rp.add_argument("--all", action="store_true")
    rp.add_argument("--missing-llm", action="store_true")
    rp.add_argument("--yes", action="store_true", help="confirm paid LLM calls")
    rp.add_argument("--id", type=int)

    pr = sub.add_parser("probe")
    pr.add_argument("url", nargs="?")
    pr.add_argument("--file", help="parse a saved HTML file instead of fetching")
    pr.add_argument("--detail", action="store_true", help="parse as a detail page")

    a = ap.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)

    if a.cmd in ("llm-check", "llm-compare"):
        from app.nlp.llm import LLMError, build_llm_analyzer, compare_methods
        from app.nlp.taxonomy import Taxonomy

        key = (
            settings.deepseek_api_key.get_secret_value().strip()
            if settings.deepseek_api_key else ""
        )

        print(
            "DEEPSEEK_API_KEY :",
            "maujood hai" if key else "NAHI MILA (.env mein DEEPSEEK_API_KEY=... daalna hai)"
        )
        print(
            "USE_LLM          :",
            settings.use_llm,
            "(istemal ke liye USE_LLM=true hona chahiye)"
        )
        print("Model            :", settings.deepseek_model)

        if not key:
            return 1

        tax = Taxonomy.from_yaml(settings.taxonomy_path)
        settings.use_llm = True
        analyzer = build_llm_analyzer(settings, tax)

        if a.cmd == "llm-check":
            try:
                res = analyzer.analyze(
                    "Construction of link road at Sakwar Gilgit",
                    "XEN B&R Division Gilgit",
                    "Sealed tenders are invited for construction of 600 feet road and a retaining wall."
                )
            except LLMError as e:
                print("Nateeja          : NAKAM -", e)
                return 1

            print("Nateeja          : THEEK")
            print("  category :", res.categories)
            print("  keywords :", res.keywords)
            return 0

        from app.db import get_session_factory

        with get_session_factory()() as session:
            rows = compare_methods(session, analyzer, tax, a.n)

        agree = sum(1 for r in rows if r["llm"] == r["taxonomy"])

        for r in rows:
            mark = "=" if r["llm"] == r["taxonomy"] else "!"
            print(
                f"{mark} #{r['id']:<4} {r['title'][:55]:55s} | "
                f"taxonomy: {r['taxonomy'][:28]:28s} | "
                f"LLM: {(r['llm'] or r.get('error', '?'))[:40]}"
            )

        print(
            f"\n{agree}/{len(rows)} mein dono ki pehli category ek jaisi. "
            "'!' wali qataron ko aankh se dekh kar batayen kaun sahi hai."
        )
        return 0

    if a.cmd == "probe":
        from app.scraper.client import SiteClient
        from app.scraper.parser import parse_detail, parse_listing
        from app.scraper.source import load_site_config

        cfg = load_site_config(settings.site_config_path)
        base = a.url or cfg["base_url"]

        html = (
            open(a.file, encoding="utf-8").read()
            if a.file
            else SiteClient(settings).get_html(base)
        )

        if a.detail:
            print(
                json.dumps(
                    parse_detail(html, base, cfg.get("detail", {})),
                    indent=2,
                    ensure_ascii=False
                )
            )
        else:
            rows, nxt = parse_listing(
                html,
                base,
                cfg.get("listing", {})
            )

            print(f"{len(rows)} rows parsed; next page: {nxt}")

            for r in rows[:10]:
                print(
                    json.dumps(
                        {k: v for k, v in r.as_dict().items() if v},
                        ensure_ascii=False
                    )
                )

            if not rows:
                print(
                    "No rows. Set listing.mode to css and configure "
                    "row/fields in config/site.yaml."
                )

        return 0

    from app.db import get_session_factory
    from app.nlp.pipeline import mark_for_reprocess, run_pending_nlp, sync_taxonomy
    from app.nlp.taxonomy import Taxonomy

    if a.cmd == "scrape":
        from app.monitor import run_once

        print(
            json.dumps(
                run_once(trigger="manual"),
                indent=2,
                default=str
            )
        )
        return 0

    if a.cmd == "extract-pending":
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from app.models import Tender, TenderDocument
        from app.scraper.source import TenderSource
        from app.monitor import sync_documents

        if not settings.download_documents:
            print("ERROR: DOWNLOAD_DOCUMENTS disabled")
            return 1

        query = (
            select(Tender)
            .where(
                Tender.documents.any(
                    TenderDocument.status == "pending"
                )
            )
            .options(selectinload(Tender.documents))
        )

        processed = 0
        failed = 0

        with get_session_factory()() as session:
            tenders = session.scalars(query).unique().all()
            source = TenderSource(settings)

            for tender in tenders:
                sync_documents(
                    session,
                    tender,
                    source,
                    settings,
                    pending_only=True
                )

                processed += sum(
                    1
                    for d in tender.documents
                    if d.status != "pending"
                    and d.downloaded_at is not None
                )

                failed += sum(
                    1
                    for d in tender.documents
                    if d.status == "failed"
                    and d.downloaded_at is not None
                )

                session.commit()

        print(
            json.dumps(
                {
                    "processed": processed,
                    "failed": failed
                },
                indent=2
            )
        )
        return 0

    if a.cmd == "extract-no-text":
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from app.models import Tender, TenderDocument
        from app.scraper.source import TenderSource
        from app.monitor import sync_documents

        if not settings.download_documents:
            print("ERROR: DOWNLOAD_DOCUMENTS disabled")
            return 1

        query = (
            select(Tender)
            .where(
                Tender.documents.any(
                    TenderDocument.status == "no_text"
                )
            )
            .options(selectinload(Tender.documents))
        )

        processed = 0
        failed = 0

        with get_session_factory()() as session:
            tenders = session.scalars(query).unique().all()
            source = TenderSource(settings)

            for tender in tenders:
                sync_documents(
                    session,
                    tender,
                    source,
                    settings,
                    retry_no_text=True
                )

                for doc in tender.documents:
                    if doc.status == "ok" and doc.text:
                        processed += 1
                    elif doc.status == "failed":
                        failed += 1

                session.commit()

        print(
            json.dumps(
                {
                    "processed": processed,
                    "failed": failed
                },
                indent=2
            )
        )
        return 0

    with get_session_factory()() as session:

        if a.cmd == "sync-taxonomy":
            sync_taxonomy(
                session,
                Taxonomy.from_yaml(settings.taxonomy_path)
            )
            session.commit()

        elif a.cmd == "reprocess":
            if not (a.all or a.missing_llm) and a.id is None:
                print(
                    "give --all, --missing-llm or --id",
                    file=sys.stderr
                )
                return 2

            from sqlalchemy import func, select
            from app.models import ProcessingStatus

            if (
                (a.all or a.missing_llm)
                and settings.use_llm
                and not a.yes
            ):
                q = select(func.count()).select_from(ProcessingStatus)

                if a.missing_llm:
                    q = q.where(
                        (ProcessingStatus.nlp_method.is_(None))
                        | (
                            ~ProcessingStatus.nlp_method.like("llm-%")
                        )
                    )

                n = session.scalar(q)

                print(
                    f"USE_LLM=true: ye {n} tenders par DeepSeek "
                    f"chalayega ({n} paid calls)."
                )
                print(
                    "Pehle `llm-compare --n 30` se nateeja dekh lein. "
                    "Pakka ho to dobara chalayen, aakhir mein --yes lagakar."
                )
                return 2

            n = mark_for_reprocess(
                session,
                None if (a.all or a.missing_llm) else a.id,
                only_without_llm=a.missing_llm
            )

            session.commit()
            print(f"marked {n} tender(s) for reprocessing")
            print(run_pending_nlp(session, settings=settings))

        elif a.cmd == "nlp":
            print(run_pending_nlp(session, settings=settings))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
