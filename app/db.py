"""Engine / session helpers."""
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine():
    url = get_settings().database_url
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
    return create_engine(url, **kwargs)


@lru_cache
def get_session_factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db():
    """FastAPI dependency: one session per request."""
    session: Session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
