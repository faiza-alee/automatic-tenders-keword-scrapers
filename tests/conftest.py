import os
from pathlib import Path

os.environ.update(DATABASE_URL="sqlite://", API_KEYS="test-key", ADMIN_API_KEYS="admin-key", SCHEDULER_ENABLED="false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.main import create_app
from app.config import Settings
from app.db import get_db
from app.models import Base
from app.nlp.taxonomy import Taxonomy

DATA = Path(__file__).parent / "data"
ROOT = Path(__file__).parent.parent


def make_settings(**kw) -> Settings:
    base = dict(database_url="sqlite://", api_keys="test-key", admin_api_keys="admin-key", request_delay_seconds=0,
                taxonomy_path=str(ROOT / "config/taxonomy.yaml"), site_config_path=str(ROOT / "config/site.yaml"),
                rate_limit_per_minute=1000, max_retries=2)
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    yield e
    e.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture
def taxonomy():
    return Taxonomy.from_yaml(str(ROOT / "config/taxonomy.yaml"))


def build_client(session_factory, settings) -> TestClient:
    app = create_app(settings)

    def _db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def api(session_factory, settings):
    c = build_client(session_factory, settings)
    c.headers.update({"X-API-Key": "test-key"})
    return c


@pytest.fixture
def admin(session_factory, settings):
    c = build_client(session_factory, settings)
    c.headers.update({"X-API-Key": "admin-key"})
    return c
