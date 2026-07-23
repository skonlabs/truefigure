"""Database access — a thin psycopg 3 layer over the Supabase Postgres connection.

A single module-level connection pool; dict rows; explicit transactions. The
server uses the direct/session connection string (workers and partition DDL need
it). Nothing here is Supabase-specific — it is a plain Postgres connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TypeAlias

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from truefigure_server.platform.config.config import get_settings

DictConn: TypeAlias = psycopg.Connection[dict[str, Any]]

_pool: ConnectionPool[DictConn] | None = None


def get_pool() -> ConnectionPool[DictConn]:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_settings().database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[DictConn]:
    """A pooled connection; commits on clean exit, rolls back on exception."""
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


@contextmanager
def transaction() -> Iterator[psycopg.Cursor[dict[str, Any]]]:
    """A transaction yielding a cursor; commit/rollback handled by the context."""
    with connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                yield cur


def fetch_one(sql: str, params: Any = None) -> dict[str, Any] | None:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    return row


def fetch_all(sql: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return rows
