from contextlib import contextmanager
import logging
from pathlib import Path
from typing import Generator

from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine

from backend.config import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)


def _sqlite_fallback_url() -> str:
    db_path = Path(__file__).resolve().parents[2] / "waypoint.sqlite3"
    return f"sqlite+pysqlite:///{db_path.as_posix()}"


def _connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("postgresql+psycopg://"):
        return {"prepare_threshold": None}
    return {}


def _build_engine(database_url: str):
    engine_kwargs: dict[str, object] = {
        "pool_pre_ping": True,
        "connect_args": _connect_args(database_url),
    }
    if database_url.startswith("postgresql+psycopg://"):
        engine_kwargs.update(
            {
                "pool_size": max(1, int(settings.db_pool_size)),
                "max_overflow": max(0, int(settings.db_max_overflow)),
                "pool_timeout": max(1, int(settings.db_pool_timeout_seconds)),
                "pool_recycle": max(30, int(settings.db_pool_recycle_seconds)),
            }
        )

    return create_engine(
        database_url,
        **engine_kwargs,
    )


engine = _build_engine(settings.database_url)
fallback_engine = _build_engine(_sqlite_fallback_url())


def _migration_database_url() -> str:
    return settings.direct_url or settings.database_url


def get_migration_engine():
    migration_url = _migration_database_url()
    return create_engine(
        migration_url,
        pool_pre_ping=True,
        connect_args=_connect_args(migration_url),
    )


def create_db_and_tables() -> None:
    try:
        migration_engine = get_migration_engine()
        SQLModel.metadata.create_all(migration_engine)
    except Exception as exc:
        logger.warning("Primary database unavailable during startup; using SQLite fallback: %s", exc)
        SQLModel.metadata.create_all(fallback_engine)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    session = Session(engine)
    try:
        try:
            session.connection()
        except OperationalError as exc:
            logger.warning("Primary database unavailable; switching to SQLite fallback: %s", exc)
            session.close()
            session = Session(fallback_engine)
        yield session
    finally:
        session.close()
