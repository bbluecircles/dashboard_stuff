"""MariaDB connection. Same env vars as db_snapshot.py, plus PD_* overrides."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import pymysql
import pymysql.cursors

from provider_directory.settings import MART_CHARSET, MART_COLLATION, MART_DB, require_ident


class ConfigError(RuntimeError):
    pass


def db_config() -> dict:
    user = os.environ.get("DB_USER")
    password = os.environ.get("DB_PASSWORD")
    missing = [name for name, value in (("DB_USER", user), ("DB_PASSWORD", password)) if not value]
    if missing:
        raise ConfigError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy them into a local .env (same keys as db_snapshot.py)."
        )
    return {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", "3306")),
        "user": user,
        "password": password,
    }


def ref_db_config() -> dict:
    """Connection for the reference server that holds master_ref (111_physician,
    cms_group_practice). REF_DB_* env; when REF_DB_HOST is unset the reference
    tables are assumed to live on the claims server and DB_* is reused."""
    host = os.environ.get("REF_DB_HOST")
    if not host:
        cfg = db_config()
        cfg["same_server"] = True
        return cfg
    user = os.environ.get("REF_DB_USER") or os.environ.get("DB_USER")
    password = os.environ.get("REF_DB_PASSWORD") or os.environ.get("DB_PASSWORD")
    missing = [
        name for name, value in (("REF_DB_USER", user), ("REF_DB_PASSWORD", password)) if not value
    ]
    if missing:
        raise ConfigError(
            "Missing required environment variables for the reference server: "
            + ", ".join(missing)
            + ". Set REF_DB_HOST, REF_DB_PORT, REF_DB_USER, REF_DB_PASSWORD in .env."
        )
    return {
        "host": host,
        "port": int(os.environ.get("REF_DB_PORT", "3306")),
        "user": user,
        "password": password,
        "same_server": False,
    }


def quote_ident(identifier: str) -> str:
    require_ident(identifier, "identifier")
    return "`" + identifier.replace("`", "``") + "`"


@contextmanager
def get_connection(*, autocommit: bool = False) -> Iterator[pymysql.Connection]:
    cfg = db_config()
    conn = pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        charset="utf8mb4",
        autocommit=autocommit,
        cursorclass=pymysql.cursors.DictCursor,
        init_command=f"SET NAMES utf8mb4 COLLATE {require_ident(MART_COLLATION, 'collation')}",
    )
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_ref_connection(*, max_statement_seconds: int | None = None) -> Iterator[pymysql.Connection]:
    """Read-only session on the reference server.

    autocommit on and READ UNCOMMITTED so a long copy never holds a snapshot or
    row locks on 111_physician; a per-statement time limit so a slow page fails
    instead of queueing behind the server's own writers. Nothing here writes.
    """
    from provider_directory.settings import REF_COPY_MAX_STATEMENT_SECONDS

    cfg = ref_db_config()
    seconds = int(max_statement_seconds or REF_COPY_MAX_STATEMENT_SECONDS)
    conn = pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
        read_timeout=max(seconds * 2, 60),
        init_command=(
            "SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED, "
            f"max_statement_time = {seconds}"
        ),
    )
    try:
        yield conn
    finally:
        conn.close()


def ensure_mart_database(conn, mart_db: str = MART_DB) -> None:
    ident = quote_ident(mart_db)
    charset = require_ident(MART_CHARSET, "charset")
    collation = require_ident(MART_COLLATION, "collation")
    with conn.cursor() as cur:
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS {ident} CHARACTER SET {charset} COLLATE {collation}"
        )
        cur.execute(f"ALTER DATABASE {ident} CHARACTER SET {charset} COLLATE {collation}")
        cur.execute(f"USE {ident}")
