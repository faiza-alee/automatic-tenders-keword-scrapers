from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings
from app.models import Base

config = context.config
target_metadata = Base.metadata
url = get_settings().database_url


def run_migrations_offline():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True,
                          render_as_batch=url.startswith("sqlite"))
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
