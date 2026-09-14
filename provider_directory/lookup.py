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
    GroupPracticeDumpList,
    GroupPracticeDumpRow,
    GroupPracticeProfile,
    HospitalAffiliation,
    ProviderDumpList,
    ProviderDumpRow,
    ProviderGroupPractice,
    ProviderPractice,
    ProviderReferral,
    ProviderSpine,
    ProviderSpineList,
    ProviderUtilization,
)
from provider_directory.settings import (
    MART_DB,
    MAX_HOSPITAL_AFFILIATIONS,
    MIN_HOSPITAL_AFFILIATION_SHARE_PCT,
)


_OPEN_PAYMENTS_MONEY = (
    "open_payments_general_total",
    "open_payments_research_total",
    "open_payments_ownership_total",
)


def _as_bool(row: dict, *flags: str) -> dict:
    for flag in flags:
        if row.get(flag) is not None:
            row[flag] = bool(row[flag])
    return row


def _missing_table(exc: BaseException) -> bool:
    return bool(exc.args) and exc.args[0] == 1146


def _null_zero_open_payments(row: dict) -> dict:
    """Missing kinds overlay as 0; API/UI must treat that as null, not $0."""
    for key in _OPEN_PAYMENTS_MONEY:
        value = row.get(key)
        if value is None:
            continue
        try:
            if float(value) == 0:
                row[key] = None
        except (TypeError, ValueError):
            pass
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
        if _missing_table(exc):
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
        if _missing_table(exc):
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
        if _missing_table(exc):
            return empty
        raise
    by_npi: dict[int, list[ProviderUtilization]] = empty
    for row in rows:
        by_npi.setdefault(int(row["npi"]), []).append(ProviderUtilization.model_validate(row))
    return by_npi


def fetch_group_practices(
    conn, npis: list[int], *, mart_db: str = MART_DB
) -> dict[int, list[ProviderGroupPractice]]:
    empty = {npi: [] for npi in npis}
    if not npis:
        return {}
    placeholders = ", ".join(["%s"] * len(npis))
    table = f"{quote_ident(mart_db)}.pd_provider_group_practice"
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT * FROM {table}
                WHERE npi IN ({placeholders})
                ORDER BY npi, org_rank
                """,
                npis,
            )
            rows = cur.fetchall()
    except pymysql.err.ProgrammingError as exc:
        if _missing_table(exc):
            return empty
        raise
    by_npi: dict[int, list[ProviderGroupPractice]] = empty
    for row in rows:
        _as_bool(row, "is_primary")
        by_npi.setdefault(int(row["npi"]), []).append(ProviderGroupPractice.model_validate(row))
    return by_npi


def fetch_hospital_affiliations(
    conn, npis: list[int], *, mart_db: str = MART_DB
) -> dict[int, list[HospitalAffiliation]]:
    empty = {npi: [] for npi in npis}
    if not npis:
        return {}
    placeholders = ", ".join(["%s"] * len(npis))
    table = f"{quote_ident(mart_db)}.pd_provider_hospital_affiliation"
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT * FROM {table}
                WHERE npi IN ({placeholders})
                ORDER BY npi, affiliation_rank
                """,
                npis,
            )
            rows = cur.fetchall()
    except pymysql.err.ProgrammingError as exc:
        if _missing_table(exc):
            return empty
        raise
    by_npi: dict[int, list[HospitalAffiliation]] = empty
    for row in rows:
        by_npi.setdefault(int(row["npi"]), []).append(HospitalAffiliation.model_validate(row))
    return by_npi


def fetch_group_hospital_affiliations(
    conn,
    organization_id: int,
    *,
    visits_total: int | None = None,
    mart_db: str = MART_DB,
    max_systems: int = MAX_HOSPITAL_AFFILIATIONS,
    min_share_pct: float = MIN_HOSPITAL_AFFILIATION_SHARE_PCT,
) -> list[HospitalAffiliation]:
    """Members' systems, re-ranked at the group. Sums can double-count visits."""
    denom = int(visits_total or 0)
    if denom <= 0:
        return []
    mart = quote_ident(mart_db)
    sep = "CHAR(31)"
    sql = f"""
        SELECT
            x.affiliation_rank,
            x.hospital_system_name,
            CASE
                WHEN NULLIF(TRIM(x.facility_name), '') IS NULL THEN NULL
                WHEN UPPER(TRIM(x.facility_name)) = UPPER(TRIM(x.hospital_system_name)) THEN NULL
                ELSE LEFT(TRIM(x.facility_name), 180)
            END AS facility_name,
            x.visits_at_system,
            ROUND(100.0 * x.visits_at_system / %s, 2) AS visit_share_pct,
            x.provider_count
        FROM (
            SELECT
                g.hospital_system_name,
                g.facility_name,
                g.visits_at_system,
                g.provider_count,
                ROW_NUMBER() OVER (
                    ORDER BY g.visits_at_system DESC, g.hospital_system_name
                ) AS affiliation_rank
            FROM (
                SELECT
                    SUBSTRING_INDEX(
                        MAX(CONCAT(LPAD(h.visits_at_system, 10, '0'), {sep}, h.hospital_system_name)),
                        {sep}, -1
                    ) AS hospital_system_name,
                    SUBSTRING_INDEX(
                        MAX(CONCAT(LPAD(h.visits_at_system, 10, '0'), {sep}, IFNULL(h.facility_name, ''))),
                        {sep}, -1
                    ) AS facility_name,
                    SUM(h.visits_at_system) AS visits_at_system,
                    COUNT(DISTINCT h.npi) AS provider_count
                FROM {mart}.pd_provider p
                INNER JOIN {mart}.pd_provider_hospital_affiliation h ON h.npi = p.npi
                WHERE p.primary_organization_id = %s
                GROUP BY UPPER(h.hospital_system_name)
            ) g
            WHERE (100.0 * g.visits_at_system / %s) >= %s
        ) x
        WHERE x.affiliation_rank <= %s
        ORDER BY x.affiliation_rank
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (denom, organization_id, denom, float(min_share_pct), int(max_systems)),
            )
            rows = cur.fetchall()
    except pymysql.err.ProgrammingError as exc:
        if _missing_table(exc):
            return []
        raise
    return [HospitalAffiliation.model_validate(row) for row in rows]


def _attach_practices(conn, items: list[ProviderSpine], *, mart_db: str = MART_DB) -> list[ProviderSpine]:
    if not items:
        return items
    npis = [item.npi for item in items]
    by_npi = fetch_practices(conn, npis, mart_db=mart_db)
    by_org = fetch_group_practices(conn, npis, mart_db=mart_db)
    by_hosp = fetch_hospital_affiliations(conn, npis, mart_db=mart_db)
    by_ref = fetch_referrals(conn, npis, mart_db=mart_db)
    by_util = fetch_utilization(conn, npis, mart_db=mart_db)
    return [
        item.model_copy(
            update={
                "practices": by_npi.get(item.npi, []),
                "group_practices": by_org.get(item.npi, []),
                "hospital_affiliations": by_hosp.get(item.npi, []),
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
    _null_zero_open_payments(row)
    item = ProviderSpine.model_validate(row)
    return _attach_practices(conn, [item], mart_db=mart_db)[0]


def _provider_filter_clauses(
    *,
    prefix: str = "",
    last_name: str | None = None,
    npi: int | None = None,
    specialty: str | None = None,
    active: bool | None = None,
    min_visits: int | None = None,
    max_visits: int | None = None,
    in_system: bool | None = None,
    organization: str | None = None,
    organization_id: int | None = None,
) -> tuple[list[str], list]:
    if min_visits is not None and max_visits is not None and min_visits > max_visits:
        raise ValueError("min_visits cannot exceed max_visits")
    p = prefix
    clauses = ["1=1"]
    params: list = []
    if npi is not None:
        clauses.append(f"{p}npi = %s")
        params.append(npi)
    if last_name:
        clauses.append(f"{p}last_name LIKE %s")
        params.append(last_name.strip() + "%")
    if specialty:
        clauses.append(
            f"({p}primary_specialty_code = %s OR {p}primary_specialty_description LIKE %s)"
        )
        params.extend([specialty, f"%{specialty.strip()}%"])
    if active is True:
        clauses.append(f"{p}active_provider = 1")
    elif active is False:
        clauses.append(f"({p}active_provider = 0 OR {p}active_provider IS NULL)")
    if min_visits is not None:
        clauses.append(f"IFNULL({p}visits_total, 0) >= %s")
        params.append(min_visits)
    if max_visits is not None:
        clauses.append(f"IFNULL({p}visits_total, 0) <= %s")
        params.append(max_visits)
    if in_system is True:
        clauses.append(f"{p}in_system_provider = 1")
    elif in_system is False:
        clauses.append(f"({p}in_system_provider = 0 OR {p}in_system_provider IS NULL)")
    if organization and organization.strip():
        clauses.append(f"{p}primary_organization_name LIKE %s")
        params.append(f"%{organization.strip()}%")
    if organization_id is not None:
        clauses.append(f"{p}primary_organization_id = %s")
        params.append(organization_id)
    return clauses, params


def search_providers(
    conn,
    *,
    last_name: str | None = None,
    npi: int | None = None,
    specialty: str | None = None,
    active: bool | None = None,
    min_visits: int | None = None,
    max_visits: int | None = None,
    limit: int = 25,
    offset: int = 0,
    in_system: bool | None = None,
    organization: str | None = None,
    mart_db: str = MART_DB,
) -> ProviderSpineList:
    clauses, params = _provider_filter_clauses(
        last_name=last_name,
        npi=npi,
        specialty=specialty,
        active=active,
        min_visits=min_visits,
        max_visits=max_visits,
        in_system=in_system,
        organization=organization,
    )
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
        _null_zero_open_payments(row)
        items.append(ProviderSpine.model_validate(row))
    return ProviderSpineList(items=_attach_practices(conn, items, mart_db=mart_db), total=total)


def list_providers(
    conn,
    *,
    last_name: str | None = None,
    npi: int | None = None,
    specialty: str | None = None,
    active: bool | None = None,
    min_visits: int | None = None,
    max_visits: int | None = None,
    limit: int = 25,
    offset: int = 0,
    in_system: bool | None = None,
    organization: str | None = None,
    city: str | None = None,
    mart_db: str = MART_DB,
    state: str | None = None,
    organization_id: int | None = None,
) -> ProviderDumpList:
    """Paged dump for the picker table. Does not attach nested practices/referrals."""
    clauses, params = _provider_filter_clauses(
        prefix="p.",
        last_name=last_name,
        npi=npi,
        specialty=specialty,
        active=active,
        min_visits=min_visits,
        max_visits=max_visits,
        in_system=in_system,
        organization=organization,
        organization_id=organization_id,
    )
    city_term = city.strip() if city else ""
    if city_term:
        clauses.append("pr.city LIKE %s")
        params.append(f"%{city_term}%")
    where = " AND ".join(clauses)
    mart = quote_ident(mart_db)
    provider = f"{mart}.pd_provider p"
    practice = f"{mart}.pd_provider_practice"
    join_sql = f"LEFT JOIN {practice} pr ON pr.npi = p.npi AND pr.site_rank = 1"
    count_from = f"{provider} {join_sql}" if city_term else provider
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {count_from} WHERE {where}", params)
        total = int(cur.fetchone()["n"])
        cur.execute(
            f"""
            SELECT
                p.npi,
                p.first_name,
                p.middle_name,
                p.last_name,
                p.credential,
                p.gender,
                p.primary_specialty_code,
                p.primary_specialty_description,
                p.primary_organization_name,
                p.visits_total,
                p.panel_size,
                p.in_system_provider,
                p.active_provider,
                p.wrvu_specialty_percentile,
                p.visits_specialty_percentile,
                p.activity_specialty_percentile,
                pr.name AS practice_name,
                pr.city,
                pr.state
            FROM {provider}
            {join_sql}
            WHERE {where}
            ORDER BY IFNULL(p.visits_total, 0) DESC, IFNULL(p.panel_size, 0) DESC,
                p.last_name, p.first_name, p.npi
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        rows = cur.fetchall()
    items = []
    for row in rows:
        _as_bool(row, "in_system_provider", "active_provider")
        items.append(ProviderDumpRow.model_validate(row))
    return ProviderDumpList(
        state=(state or "").upper() or mart_db.split("_")[0].upper(),
        mart_db=mart_db,
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


def _group_dump_state(state: str | None, mart_db: str) -> str:
    return (state or "").upper() or mart_db.split("_")[0].upper()


def list_group_practices(
    conn,
    *,
    organization: str | None = None,
    organization_id: int | None = None,
    parent: str | None = None,
    active: bool | None = None,
    min_visits: int | None = None,
    max_visits: int | None = None,
    min_providers: int | None = None,
    in_system: bool | None = None,
    limit: int = 25,
    offset: int = 0,
    mart_db: str = MART_DB,
    state: str | None = None,
) -> GroupPracticeDumpList:
    """Paged dump of group practices. Reads pd_provider only — never pat_dt.

    Grain is primary_organization_id (billing NPI from physician_primary_affiliation).
    visits_total / panel_size / wrvu_total are sums of member Type 1 NPIs and can
    double-count an encounter billed by two members of the same group.
    """
    if min_visits is not None and max_visits is not None and min_visits > max_visits:
        raise ValueError("min_visits cannot exceed max_visits")
    member_clauses = ["p.primary_organization_id IS NOT NULL"]
    member_params: list = []
    if active is True:
        member_clauses.append("p.active_provider = 1")
    elif active is False:
        member_clauses.append("(p.active_provider = 0 OR p.active_provider IS NULL)")
    member_where = " AND ".join(member_clauses)

    outer_clauses = ["1=1"]
    outer_params: list = []
    if organization_id is not None:
        outer_clauses.append("s.organization_id = %s")
        outer_params.append(organization_id)
    if organization and organization.strip():
        outer_clauses.append("s.organization_name LIKE %s")
        outer_params.append(f"%{organization.strip()}%")
    if parent and parent.strip():
        outer_clauses.append("s.parent_name LIKE %s")
        outer_params.append(f"%{parent.strip()}%")
    if min_visits is not None:
        outer_clauses.append("s.visits_total >= %s")
        outer_params.append(min_visits)
    if max_visits is not None:
        outer_clauses.append("s.visits_total <= %s")
        outer_params.append(max_visits)
    if min_providers is not None:
        outer_clauses.append("s.provider_count >= %s")
        outer_params.append(min_providers)
    if in_system is True:
        outer_clauses.append("s.in_system_provider_count >= 1")
    elif in_system is False:
        outer_clauses.append("s.in_system_provider_count = 0")
    outer_where = " AND ".join(outer_clauses)

    mart = quote_ident(mart_db)
    provider = f"{mart}.pd_provider p"
    grouped = f"""
        SELECT
            p.primary_organization_id AS organization_id,
            MAX(p.primary_organization_name) AS organization_name,
            MAX(p.primary_organization_npi) AS organization_npi,
            MAX(p.primary_organization_parent_id) AS parent_id,
            MAX(p.primary_organization_parent_name) AS parent_name,
            COUNT(*) AS provider_count,
            SUM(CASE WHEN p.active_provider = 1 THEN 1 ELSE 0 END) AS active_provider_count,
            SUM(CASE WHEN p.in_system_provider = 1 THEN 1 ELSE 0 END) AS in_system_provider_count,
            SUM(IFNULL(p.visits_total, 0)) AS visits_total,
            SUM(IFNULL(p.panel_size, 0)) AS panel_size,
            SUM(IFNULL(p.wrvu_total, 0)) AS wrvu_total
        FROM {provider}
        WHERE {member_where}
        GROUP BY p.primary_organization_id
    """
    scored = f"""
        SELECT
            g.*,
            ROUND(g.visits_total * 1.0 / NULLIF(g.provider_count, 0), 1) AS visits_per_provider,
            CASE
                WHEN g.visits_total > 0 THEN ROUND(
                    100.0 * ROW_NUMBER() OVER (
                        PARTITION BY (g.visits_total > 0)
                        ORDER BY g.visits_total, g.organization_id
                    ) / COUNT(*) OVER (PARTITION BY (g.visits_total > 0)),
                    1
                )
            END AS visits_percentile,
            CASE
                WHEN g.visits_total > 0 THEN ROUND(
                    100.0 * ROW_NUMBER() OVER (
                        PARTITION BY (g.visits_total > 0)
                        ORDER BY g.visits_total * 1.0 / NULLIF(g.provider_count, 0), g.organization_id
                    ) / COUNT(*) OVER (PARTITION BY (g.visits_total > 0)),
                    1
                )
            END AS activity_percentile
        FROM ({grouped}) g
    """
    filtered = f"SELECT * FROM ({scored}) s WHERE {outer_where}"
    all_params = [*member_params, *outer_params]
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM ({filtered}) x", all_params)
        total = int(cur.fetchone()["n"])
        cur.execute(
            f"""
            {filtered}
            ORDER BY visits_total DESC, provider_count DESC, organization_name, organization_id
            LIMIT %s OFFSET %s
            """,
            [*all_params, limit, offset],
        )
        rows = cur.fetchall()
    items = [GroupPracticeDumpRow.model_validate(row) for row in rows]
    return GroupPracticeDumpList(
        state=_group_dump_state(state, mart_db),
        mart_db=mart_db,
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        visits_are_summed_across_npis=True,
    )


def get_group_practice(
    conn,
    organization_id: int,
    *,
    mart_db: str = MART_DB,
    state: str | None = None,
) -> GroupPracticeProfile | None:
    result = list_group_practices(
        conn,
        organization_id=organization_id,
        limit=1,
        offset=0,
        mart_db=mart_db,
        state=state,
    )
    if not result.items:
        return None
    row = result.items[0]
    return GroupPracticeProfile(
        **row.model_dump(),
        hospital_affiliations=fetch_group_hospital_affiliations(
            conn,
            organization_id,
            visits_total=row.visits_total,
            mart_db=mart_db,
        ),
    )

