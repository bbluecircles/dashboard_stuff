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
