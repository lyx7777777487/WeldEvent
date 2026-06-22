"""Async engine + session factory (Phase 1 stub).

In Phase 2 this module owns the SQLAlchemy AsyncEngine and async_sessionmaker.
For Phase 1 it provides a typed placeholder so composition-root code can
construct dependencies without a live database.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class DatabaseConfig:
    """Connection settings — populated from env in production."""

    dsn: str = "postgresql+asyncpg://localhost/weldevent"
    pool_size: int = 10
    max_overflow: int = 5
    echo: bool = False


class StubAsyncSession:
    """Placeholder for sqlalchemy.ext.asyncio.AsyncSession.

    Implements the minimum surface (`commit`, `rollback`, `close`) needed for
    code that wants to assert "no errors raised" against a real session, but
    performs no I/O.
    """

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def close(self) -> None: ...
    async def execute(self, *_args, **_kwargs) -> None:
        return None


class AsyncDatabaseEngine:
    """Phase 1 stub. Real impl in Phase 2 wraps SQLAlchemy AsyncEngine."""

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config

    @property
    def config(self) -> DatabaseConfig:
        return self._config

    @asynccontextmanager
    async def session(self) -> AsyncIterator[StubAsyncSession]:
        sess = StubAsyncSession()
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        finally:
            await sess.close()

    async def health_check(self) -> bool:
        return True

    async def dispose(self) -> None:
        return None
