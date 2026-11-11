from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


def utc_iso(moment: datetime | None = None) -> str:
    """ISO 8601 UTC at fixed width, so stored values also compare correctly as text."""
    return (moment or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")


def make_engine(url: str) -> Engine:
    # FastAPI runs sync routes in a thread pool, so a connection can change threads.
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def use_wal(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")

    return engine


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        yield session
