"""Read path the FastAPI layer should call.

    from provider_directory.db import get_connection
    from provider_directory.lookup import get_provider

    with get_connection() as conn:
        return get_provider(conn, npi)
"""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.models import ProviderSpine, ProviderSpineList
from provider_directory.settings import MART_DB


def get_provider(conn, npi: int, *, mart_db: str = MART_DB) -> ProviderSpine | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM {quote_ident(mart_db)}.pd_provider WHERE npi = %s",
            (npi,),
        )
        row = cur.fetchone()
    if not row:
        return None
    for flag in ("in_system_provider", "active_provider"):
        if row.get(flag) is not None:
            row[flag] = bool(row[flag])
    return ProviderSpine.model_validate(row)


def search_providers(
    conn,
    *,
    last_name: str | None = None,
    npi: int | None = None,
    specialty: str | None = None,
    active: bool | None = None,
    min_visits: int | None = None,
    limit: int = 25,
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
            LIMIT %s
            """,
            [*params, limit],
        )
        rows = cur.fetchall()
    items = []
    for row in rows:
        for flag in ("in_system_provider", "active_provider"):
            if row.get(flag) is not None:
                row[flag] = bool(row[flag])
        items.append(ProviderSpine.model_validate(row))
    return ProviderSpineList(items=items, total=total)
