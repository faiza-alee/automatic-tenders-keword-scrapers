import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).parent.parent
EXPECTED = {"tenders", "departments", "categories", "tender_documents", "keywords", "tender_keywords",
            "processing_status", "scraping_logs", "tender_categories", "tender_changes"}


def test_alembic_upgrade_and_downgrade(tmp_path):
    db = tmp_path / "m.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "alembic", *a], cwd=ROOT, env=env, capture_output=True, text=True)
    r = run("upgrade", "head")
    assert r.returncode == 0, r.stderr
    assert EXPECTED <= set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert run("downgrade", "base").returncode == 0


def test_models_match_migration(tmp_path):
    """Autogenerate against a freshly migrated DB must find nothing to change."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.models import Base
    db = tmp_path / "d.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}"}
    assert subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env).returncode == 0
    with create_engine(f"sqlite:///{db}").connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
    assert diff == []
