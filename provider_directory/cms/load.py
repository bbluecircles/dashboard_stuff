"""Bulk-load CMS files into cms_pdc_* / cms_nppes_type1."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from provider_directory.cms.parse import (
    iter_local_csv,
    iter_nppes_from_zip,
    keep_nppes_row,
    keep_pdc_clinician_row,
    parse_facility_row,
    parse_nppes_row,
    parse_pdc_clinician_row,
)
from provider_directory.db import quote_ident
from provider_directory.settings import MART_DB

BATCH = 1000

CLINICIAN_COLS = (
    "npi",
    "ind_pac_id",
    "ind_enrl_id",
    "last_name",
    "first_name",
    "middle_name",
    "suffix",
    "gender",
    "credential",
    "med_sch",
    "grd_yr",
    "pri_spec",
    "telehlth",
    "org_pac_id",
    "num_org_mem",
    "adr_ln_1",
    "adr_ln_2",
    "city",
    "state",
    "zip",
    "phone",
    "adrs_id",
)

FACILITY_COLS = (
    "npi",
    "ind_pac_id",
    "last_name",
    "first_name",
    "facility_type",
    "ccn",
    "facility_type_ccn",
)

NPPES_COLS = (
    "npi",
    "last_name",
    "first_name",
    "middle_name",
    "suffix",
    "credential",
    "gender",
    "primary_taxonomy",
    "practice_state",
    "mailing_state",
    "last_update_date",
    "deactivation_date",
    "sole_proprietor",
)


def _insert_sql(mart_db: str, table: str, cols: tuple[str, ...], replace: bool = False) -> str:
    db = quote_ident(mart_db)
    tbl = quote_ident(table)
    col_sql = ", ".join(quote_ident(c) for c in cols)
    placeholders = ", ".join(f"%({c})s" for c in cols)
    verb = "REPLACE INTO" if replace else "INSERT INTO"
    return f"{verb} {db}.{tbl} ({col_sql}) VALUES ({placeholders})"


def _flush(cur, sql: str, batch: list[dict]) -> int:
    if not batch:
        return 0
    cur.executemany(sql, batch)
    n = len(batch)
    batch.clear()
    return n


def load_spine_npis(conn, mart_db: str = MART_DB) -> set[int]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT npi FROM {quote_ident(mart_db)}.pd_provider")
        return {int(row["npi"]) for row in cur.fetchall()}


def _stream_insert(
    conn,
    sql: str,
    rows: Iterable[dict],
    *,
    commit_every: int = 5000,
) -> int:
    inserted = 0
    since_commit = 0
    batch: list[dict] = []
    with conn.cursor() as cur:
        for row in rows:
            batch.append(row)
            if len(batch) >= BATCH:
                inserted += _flush(cur, sql, batch)
                since_commit += BATCH
                if since_commit >= commit_every:
                    conn.commit()
                    since_commit = 0
        inserted += _flush(cur, sql, batch)
    conn.commit()
    return inserted


def load_pdc_clinician(
    conn,
    path: Path,
    spine_npis: set[int] | None,
    *,
    mart_db: str = MART_DB,
    truncate: bool = True,
) -> int:
    sql = _insert_sql(mart_db, "cms_pdc_clinician", CLINICIAN_COLS)
    if truncate:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {quote_ident(mart_db)}.cms_pdc_clinician")
        conn.commit()

    def rows():
        for raw in iter_local_csv(path):
            parsed = parse_pdc_clinician_row(raw)
            if parsed and keep_pdc_clinician_row(parsed, spine_npis):
                yield parsed

    return _stream_insert(conn, sql, rows())


def load_pdc_facility(
    conn,
    path: Path,
    spine_npis: set[int] | None,
    *,
    mart_db: str = MART_DB,
    truncate: bool = True,
) -> int:
    sql = _insert_sql(mart_db, "cms_pdc_facility_affil", FACILITY_COLS)
    if truncate:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {quote_ident(mart_db)}.cms_pdc_facility_affil")
        conn.commit()

    def rows():
        for raw in iter_local_csv(path):
            parsed = parse_facility_row(raw)
            if not parsed:
                continue
            if spine_npis is not None and parsed["npi"] not in spine_npis:
                continue
            yield parsed

    return _stream_insert(conn, sql, rows())


def load_nppes(
    conn,
    path: Path,
    spine_npis: set[int] | None,
    *,
    mart_db: str = MART_DB,
    truncate: bool = True,
) -> int:
    sql = _insert_sql(mart_db, "cms_nppes_type1", NPPES_COLS, replace=True)
    if truncate:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {quote_ident(mart_db)}.cms_nppes_type1")
        conn.commit()

    def rows():
        for raw in iter_nppes_from_zip(path):
            parsed = parse_nppes_row(raw)
            if parsed and keep_nppes_row(parsed, spine_npis):
                yield parsed

    return _stream_insert(conn, sql, rows())
