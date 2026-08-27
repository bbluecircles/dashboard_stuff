#!/usr/bin/env python3
"""Snapshot a fixed set of MariaDB databases into a single JSON file.

For each database in DB_DATABASES, records every base table's name,
exact row count, column metadata, and first 5 rows (unordered).

Output structure:
{
  "generated_at": "...",
  "server": "host:port",
  "databases": {
    "<db_name>": {
      "tables": {
        "<table_name>": {
          "row_count": 123,
          "columns": [
            {"name": "...", "type": "...", "nullable": true, "key": "PRI"}
          ],
          "sample_rows": [ {...}, ... ]
        }
      }
    }
  }
}

Connection details come from environment variables or a .env file in the
current directory (real environment variables take precedence):
  DB_HOST        (default: localhost)
  DB_PORT        (default: 3306)
  DB_USER        (required)
  DB_PASSWORD    (required)
  DB_DATABASES   (required, comma-separated, e.g. "db_one,db_two,db_three")
  DB_SNAPSHOT_OUT (default: db_snapshot.json)

Requires: pip install pymysql python-dotenv
"""

import json
import os
import sys
from datetime import datetime, timezone

import pymysql
import pymysql.cursors

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # python-dotenv not installed; fall back to real environment variables only.
    pass

SAMPLE_LIMIT = 5


def get_config():
    user = os.environ.get("DB_USER")
    password = os.environ.get("DB_PASSWORD")
    databases_raw = os.environ.get("DB_DATABASES", "")

    missing = [
        name
        for name, value in (("DB_USER", user), ("DB_PASSWORD", password), ("DB_DATABASES", databases_raw))
        if not value
    ]
    if missing:
        sys.exit(f"Missing required environment variables: {', '.join(missing)}")

    databases = [d.strip() for d in databases_raw.split(",") if d.strip()]
    if not databases:
        sys.exit("DB_DATABASES must name at least one database")

    return {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", "3306")),
        "user": user,
        "password": password,
        "databases": databases,
        "out": os.environ.get("DB_SNAPSHOT_OUT", "db_snapshot.json"),
    }


def quote_ident(identifier):
    # Backtick-quote a table/database name. Only applied to names read from
    # information_schema, but quote defensively anyway.
    return "`" + identifier.replace("`", "``") + "`"


def snapshot_database(conn, db_name):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT TABLE_NAME
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
            """,
            (db_name,),
        )
        table_names = [row["TABLE_NAME"] for row in cur.fetchall()]

        cur.execute(
            """
            SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """,
            (db_name,),
        )
        columns_by_table = {}
        for row in cur.fetchall():
            columns_by_table.setdefault(row["TABLE_NAME"], []).append(
                {
                    "name": row["COLUMN_NAME"],
                    "type": row["COLUMN_TYPE"],
                    "nullable": row["IS_NULLABLE"] == "YES",
                    "key": row["COLUMN_KEY"] or None,
                }
            )

        tables = {}
        for table_name in table_names:
            entry = {"columns": columns_by_table.get(table_name, [])}
            try:
                cur.execute(
                    f"SELECT COUNT(*) AS n FROM {quote_ident(db_name)}.{quote_ident(table_name)}"
                )
                entry["row_count"] = cur.fetchone()["n"]

                cur.execute(
                    f"SELECT * FROM {quote_ident(db_name)}.{quote_ident(table_name)} LIMIT {SAMPLE_LIMIT}"
                )
                entry["sample_rows"] = cur.fetchall()
            except pymysql.MySQLError as exc:
                # One bad table (permissions, corruption) shouldn't kill the run.
                entry["row_count"] = None
                entry["sample_rows"] = []
                entry["error"] = str(exc)
            tables[table_name] = entry

    return {"tables": tables}


def main():
    cfg = get_config()
    conn = pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        cursorclass=pymysql.cursors.DictCursor,
    )

    try:
        snapshot = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "server": f"{cfg['host']}:{cfg['port']}",
            "databases": {
                db: snapshot_database(conn, db) for db in cfg["databases"]
            },
        }
    finally:
        conn.close()

    with open(cfg["out"], "w", encoding="utf-8") as f:
        # default=str handles datetime/Decimal/etc. that JSON can't encode natively.
        json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")

    print(f"Wrote snapshot of {len(cfg['databases'])} database(s) to {cfg['out']}")


if __name__ == "__main__":
    main()
