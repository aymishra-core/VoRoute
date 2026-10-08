"""Engine and migrations. SQLite locally, Postgres when DATABASE_URL says so."""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from voroute.config import settings

_ROOT = Path(__file__).resolve().parents[2]
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_prepared_url: str | None = None


def normalized_database_url(url: str | None = None) -> str:
    """Render hands out postgres://. SQLAlchemy needs the psycopg driver name."""

    raw = settings.database_url if url is None else url
    if raw.startswith("postgres://"):
        return "postgresql+psycopg://" + raw.removeprefix("postgres://")
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg://" + raw.removeprefix("postgresql://")
    return raw


def _enable_sqlite_immediate(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection: object) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")  # type: ignore[attr-defined]


def reset_database() -> None:
    """Drop the cached engine so the next prepare uses the current URL."""

    global _engine, _session_factory, _prepared_url
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
    _prepared_url = None


def _build_engine(url: str) -> Engine:
    kwargs: dict[str, object] = {}
    if url.startswith("sqlite"):
        from sqlalchemy.pool import NullPool

        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = NullPool
    engine = create_engine(url, **kwargs)
    _enable_sqlite_immediate(engine)
    return engine


def session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        raise RuntimeError("database is not prepared")
    return _session_factory


def prepare_database() -> None:
    """Create the engine, apply Alembic, and ensure the pilot merchant exists."""

    global _engine, _session_factory, _prepared_url
    url = normalized_database_url()
    if _prepared_url == url and _session_factory is not None:
        return
    reset_database()
    _engine = _build_engine(url)
    _session_factory = sessionmaker(_engine, expire_on_commit=False)
    _upgrade(url)
    _prepared_url = url
    if settings.voroute_api_key.strip():
        from voroute.store import ensure_pilot_merchant

        ensure_pilot_merchant()


def _upgrade(url: str) -> None:
    from alembic.config import Config

    from alembic import command

    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
