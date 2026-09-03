"""Read path the FastAPI layer should call. Reads az_pd only — never az.pat_dt.

    from provider_directory.db import get_connection
    from provider_directory.lookup import get_provider

    with get_connection() as conn:
        return get_provider(conn, npi)
"""

from __future__ import annotations

import pymysql

from provider_directory.db import quote_ident
from provider_directory.models import (
    ProviderPractice,
    ProviderReferral,
    ProviderSpine,
    ProviderSpineList,
    ProviderUtilization,
)
from provider_directory.settings import MART_DB


def _as_bool(row: dict, *flags: str) -> dict:
    for flag in flags:
        if row.get(flag) is not None:
            row[flag] = bool(row[flag])
    return row


def _practice_from_row(row: dict) -> ProviderPractice:
    _as_bool(row, "needs_geocode")
    return ProviderPractice.model_validate(row)


def fetch_practices(conn, npis: list[int], *, mart_db: str = MART_DB) -> dict[int, list[ProviderPractice]]:
    empty = {npi: [] for npi in npis}
    if not npis:
        return {}
    placeholders = ", ".join(["%s"] * len(npis))
    table = f"{quote_ident(mart_db)}.pd_provider_practice"
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT * FROM {table}
                WHERE npi IN ({placeholders})
                ORDER BY npi, site_rank
                """,
                npis,
            )
            rows = cur.fetchall()
    except pymysql.err.ProgrammingError as exc:
        if exc.args and exc.args[0] == 1146:
            return empty
        raise
    by_npi: dict[int, list[ProviderPractice]] = empty
    for row in rows:
        by_npi.setdefault(int(row["npi"]), []).append(_practice_from_row(row))
    return by_npi


def _referral_from_row(row: dict) -> ProviderReferral:
    return ProviderReferral.model_validate(row)


def fetch_referrals(conn, npis: list[int], *, mart_db: str = MART_DB) -> dict[int, list[ProviderReferral]]:
    empty = {npi: [] for npi in npis}
    if not npis:
        return {}
    placeholders = ", ".join(["%s"] * len(npis))
    table = f"{quote_ident(mart_db)}.pd_provider_referral"
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT * FROM {table}
                WHERE npi IN ({placeholders})
                ORDER BY npi, direction, peer_rank
                """,
                npis,
            )
            rows = cur.fetchall()
    except pymysql.err.ProgrammingError as exc:
        if exc.args and exc.args[0] == 1146:
            return empty
        raise
    by_npi: dict[int, list[ProviderReferral]] = empty
    for row in rows:
        by_npi.setdefault(int(row["npi"]), []).append(_referral_from_row(row))
    return by_npi


def fetch_utilization(conn, npis: list[int], *, mart_db: str = MART_DB) -> dict[int, list[ProviderUtilization]]:
    empty = {npi: [] for npi in npis}
    if not npis:
        return {}
    placeholders = ", ".join(["%s"] * len(npis))
    table = f"{quote_ident(mart_db)}.pd_provider_utilization"
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT * FROM {table}
                WHERE npi IN ({placeholders})
                ORDER BY npi, rk
                """,
                npis,
            )
            rows = cur.fetchall()
    except pymysql.err.ProgrammingError as exc:
        if exc.args and exc.args[0] == 1146:
            return empty
        raise
    by_npi: dict[int, list[ProviderUtilization]] = empty
    for row in rows:
        by_npi.setdefault(int(row["npi"]), []).append(ProviderUtilization.model_validate(row))
    return by_npi


def _attach_practices(conn, items: list[ProviderSpine], *, mart_db: str = MART_DB) -> list[ProviderSpine]:
    if not items:
        return items
    npis = [item.npi for item in items]
    by_npi = fetch_practices(conn, npis, mart_db=mart_db)
    by_ref = fetch_referrals(conn, npis, mart_db=mart_db)
    by_util = fetch_utilization(conn, npis, mart_db=mart_db)
    return [
        item.model_copy(
            update={
                "practices": by_npi.get(item.npi, []),
                "referrals": by_ref.get(item.npi, []),
                "utilization": by_util.get(item.npi, []),
            }
        )
        for item in items
    ]


def get_provider(conn, npi: int, *, mart_db: str = MART_DB) -> ProviderSpine | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM {quote_ident(mart_db)}.pd_provider WHERE npi = %s",
            (npi,),
        )
        row = cur.fetchone()
    if not row:
        return None
    _as_bool(row, "in_system_provider", "active_provider", "telehealth_offered")
    item = ProviderSpine.model_validate(row)
    return _attach_practices(conn, [item], mart_db=mart_db)[0]


def search_providers(
    conn,
    *,
    last_name: str | None = None,
    npi: int | None = None,
    specialty: str | None = None,
    active: bool | None = None,
    min_visits: int | None = None,
    limit: int = 25,
    offset: int = 0,
    in_system: bool | None = None,
    mart_db: str = MART_DB,
) -> ProviderSpineList:
    clauses = ["1=1"]
    params: list = []
    if npi is not None:
        clauses.append("npi = %s")
        params.append(npi)
    if last_name:
        clauses.append("last_name LIKE %s")
        params.append(last_name.strip() + "%")
    if specialty:
        clauses.append("(primary_specialty_code = %s OR primary_specialty_description LIKE %s)")
        params.extend([specialty, f"%{specialty}%"])
    if active is True:
        clauses.append("active_provider = 1")
    elif active is False:
        clauses.append("(active_provider = 0 OR active_provider IS NULL)")
    if min_visits is not None:
        clauses.append("IFNULL(visits_total, 0) >= %s")
        params.append(min_visits)
    if in_system is True:
        clauses.append("in_system_provider = 1")
    elif in_system is False:
        clauses.append("(in_system_provider = 0 OR in_system_provider IS NULL)")
    where = " AND ".join(clauses)
    table = f"{quote_ident(mart_db)}.pd_provider"
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params)
        total = int(cur.fetchone()["n"])
        cur.execute(
            f"""
            SELECT * FROM {table}
            WHERE {where}
            ORDER BY IFNULL(visits_total, 0) DESC, IFNULL(panel_size, 0) DESC, last_name, first_name, npi
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        rows = cur.fetchall()
    items = []
    for row in rows:
        _as_bool(row, "in_system_provider", "active_provider", "telehealth_offered")
        items.append(ProviderSpine.model_validate(row))
    return ProviderSpineList(items=_attach_practices(conn, items, mart_db=mart_db), total=total)
