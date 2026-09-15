"""Query-time group profile: same fields as a provider, summed across members.

Reads {st}_pd only. Nested lists and totals can double-count an encounter
billed by two members of the same group.
"""

from __future__ import annotations

import pymysql

from provider_directory.db import quote_ident
from provider_directory.models import (
    GroupPracticeDumpRow,
    GroupPracticeProfile,
    ProviderPractice,
    ProviderReferral,
)
from provider_directory.settings import (
    MART_DB,
    MAX_PRACTICE_SITES,
    MAX_REFERRAL_PEERS,
    PAYOR_COMMERCIAL,
)

VISIT_WEIGHTED = (
    "visits_percent_third_party",
    "visits_percent_medicaid",
    "visits_percent_medicare_advantage",
    "visits_percent_medicare_traditional",
    "visits_percent_monday",
    "visits_percent_tuesday",
    "visits_percent_wednesday",
    "visits_percent_thursday",
    "visits_percent_friday",
    "visits_percent_saturday",
    "visits_percent_sunday",
    "visits_percent_office",
    "visits_percent_hopd",
    "visits_percent_asc",
    "visits_percent_ed",
    "visits_percent_telehealth",
    "visits_percent_inpatient",
    "visits_percent_lab",
    "visits_percent_other_pos",
)

PANEL_WEIGHTED = (
    "panel_average_age",
    "panel_percent_age_0_19",
    "panel_percent_age_20_44",
    "panel_percent_age_45_64",
    "panel_percent_age_65_84",
    "panel_percent_age_85_plus",
    "panel_percent_female",
    "panel_percent_male",
)

SITE_DOW = (
    "visits_percent_monday",
    "visits_percent_tuesday",
    "visits_percent_wednesday",
    "visits_percent_thursday",
    "visits_percent_friday",
    "visits_percent_saturday",
    "visits_percent_sunday",
)


def _missing_table(exc: BaseException) -> bool:
    return bool(exc.args) and exc.args[0] == 1146


def _as_bool(row: dict, *flags: str) -> dict:
    for flag in flags:
        if row.get(flag) is not None:
            row[flag] = bool(row[flag])
    return row


def _null_zero_open_payments(row: dict) -> dict:
    for key in (
        "open_payments_general_total",
        "open_payments_research_total",
        "open_payments_ownership_total",
    ):
        value = row.get(key)
        if value is None:
            continue
        try:
            if float(value) == 0:
                row[key] = None
        except (TypeError, ValueError):
            pass
    return row


def weighted_pct_sql(column: str, weight: str) -> str:
    """Visit- or panel-weighted mean of a member percent. Null if no weights."""
    return f"""
        ROUND(
            SUM(CASE WHEN p.{column} IS NOT NULL THEN p.{column} * IFNULL(p.{weight}, 0) END)
            / NULLIF(SUM(CASE WHEN p.{column} IS NOT NULL THEN IFNULL(p.{weight}, 0) END), 0),
            2
        ) AS {column}
    """


def pick_top_sql(alias: str, column: str, visits: str = "visits_at_site") -> str:
    sep = "CHAR(31)"
    return f"""
        SUBSTRING_INDEX(
            MAX(CONCAT(LPAD(IFNULL({alias}.{visits}, 0), 10, '0'), {sep}, IFNULL(CAST({alias}.{column} AS CHAR), ''))),
            {sep}, -1
        )
    """


def _fetch(conn, sql: str, params: tuple):
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except pymysql.err.ProgrammingError as exc:
        if _missing_table(exc):
            return []
        raise


def _fetchone(conn, sql: str, params: tuple) -> dict | None:
    rows = _fetch(conn, sql, params)
    return rows[0] if rows else None


def fetch_group_metrics(conn, organization_id: int, *, mart_db: str = MART_DB) -> dict:
    mart = quote_ident(mart_db)
    visit_sql = ",\n".join(weighted_pct_sql(col, "visits_total") for col in VISIT_WEIGHTED)
    panel_sql = ",\n".join(weighted_pct_sql(col, "panel_size") for col in PANEL_WEIGHTED)
    sql = f"""
        SELECT
            SUM(IFNULL(p.visits_new_patient, 0)) AS visits_new_patient,
            SUM(IFNULL(p.visits_established, 0)) AS visits_established,
            SUM(IFNULL(p.wrvu_procedure_count, 0)) AS wrvu_procedure_count,
            SUM(IFNULL(p.wrvu_prior_year_total, 0)) AS wrvu_prior_year_total,
            SUM(IFNULL(p.wrvu_prior_year_procedure_count, 0)) AS wrvu_prior_year_procedure_count,
            SUM(IFNULL(p.open_payments_general_total, 0)) AS open_payments_general_total,
            SUM(IFNULL(p.open_payments_research_total, 0)) AS open_payments_research_total,
            SUM(IFNULL(p.open_payments_ownership_total, 0)) AS open_payments_ownership_total,
            SUM(IFNULL(p.open_payments_count, 0)) AS open_payments_count,
            MAX(p.open_payments_year) AS open_payments_year,
            MAX(p.group_size) AS group_size,
            MAX(p.telehealth_offered) AS telehealth_offered,
            (
                SELECT COUNT(DISTINCT pr.cluster_key)
                FROM {mart}.pd_provider_practice pr
                INNER JOIN {mart}.pd_provider p2 ON p2.npi = pr.npi
                WHERE p2.primary_organization_id = %s
            ) AS provider_practices_total,
            {visit_sql},
            {panel_sql}
        FROM {mart}.pd_provider p
        WHERE p.primary_organization_id = %s
    """
    row = _fetchone(conn, sql, (organization_id, organization_id)) or {}
    new_n = int(row.get("visits_new_patient") or 0)
    est_n = int(row.get("visits_established") or 0)
    em = new_n + est_n
    row["visits_new_patient"] = new_n or None
    row["visits_established"] = est_n or None
    row["visits_percent_new_patient"] = round(100.0 * new_n / em, 2) if em else None
    wrvu_n = int(row.get("wrvu_procedure_count") or 0)
    prior_n = int(row.get("wrvu_prior_year_procedure_count") or 0)
    row["wrvu_procedure_count"] = wrvu_n or None
    row["wrvu_prior_year_procedure_count"] = prior_n or None
    prior_total = row.get("wrvu_prior_year_total")
    if prior_n and prior_total is not None:
        row["wrvu_prior_year_average"] = round(float(prior_total) / prior_n, 3)
    else:
        row["wrvu_prior_year_average"] = None
        if not prior_total:
            row["wrvu_prior_year_total"] = None
    if row.get("open_payments_count") == 0:
        row["open_payments_count"] = None
    _null_zero_open_payments(row)
    _as_bool(row, "telehealth_offered")
    return row


def fetch_group_modal_specialty(conn, organization_id: int, *, mart_db: str = MART_DB) -> dict:
    mart = quote_ident(mart_db)
    sql = f"""
        SELECT
            p.primary_specialty_code,
            p.primary_specialty_description
        FROM {mart}.pd_provider p
        WHERE p.primary_organization_id = %s
          AND NULLIF(TRIM(p.primary_specialty_description), '') IS NOT NULL
        GROUP BY p.primary_specialty_code, p.primary_specialty_description
        ORDER BY SUM(IFNULL(p.visits_total, 0)) DESC, COUNT(*) DESC
        LIMIT 1
    """
    return _fetchone(conn, sql, (organization_id,)) or {}


def fetch_group_top_codes(
    conn,
    organization_id: int,
    *,
    table: str,
    prefix: str,
    visits_total: int | None,
    mart_db: str = MART_DB,
) -> dict:
    """Top 3 from members' stored top codes (pd_stg_top_*). Stay off visit staging on GET.

    visit_count on those rows is the NPI's true count for that code. Summing them
    undercounts members who had the code outside their personal top 3. Percent is
    that sum / group visits_total (lower bound of the org-wide share).
    """
    if table not in ("pd_stg_top_dx", "pd_stg_top_px"):
        raise ValueError(f"table must be pd_stg_top_dx or pd_stg_top_px, not {table!r}")
    mart = quote_ident(mart_db)
    denom = int(visits_total or 0)
    sql = f"""
        SELECT
            ranked.code,
            ranked.name,
            CASE WHEN %s > 0 THEN ROUND(100.0 * ranked.visit_count / %s, 2) END AS pct
        FROM (
            SELECT
                d.code,
                MAX(d.name) AS name,
                SUM(d.visit_count) AS visit_count
            FROM {mart}.pd_provider p
            INNER JOIN {mart}.{quote_ident(table)} d ON d.npi = p.npi
            WHERE p.primary_organization_id = %s
              AND NULLIF(TRIM(d.code), '') IS NOT NULL
            GROUP BY d.code
            ORDER BY visit_count DESC, d.code
            LIMIT 3
        ) ranked
    """
    rows = _fetch(conn, sql, (denom, denom, organization_id))
    out: dict = {}
    for i, row in enumerate(rows, start=1):
        out[f"{prefix}_{i}"] = row.get("code")
        out[f"{prefix}_{i}_name"] = row.get("name")
        out[f"{prefix}_{i}_percent"] = row.get("pct")
    return out


def fetch_group_top_payers(conn, organization_id: int, *, mart_db: str = MART_DB) -> dict:
    mart = quote_ident(mart_db)
    sql = f"""
        SELECT s.payor_parent_name, SUM(s.claim_count) AS claim_count
        FROM {mart}.pd_stg_npi_payor s
        INNER JOIN {mart}.pd_provider p ON p.npi = s.npi
        WHERE p.primary_organization_id = %s
          AND s.is_payor_code = {int(PAYOR_COMMERCIAL)}
        GROUP BY s.payor_parent_name
        ORDER BY claim_count DESC, s.payor_parent_name
    """
    rows = _fetch(conn, sql, (organization_id,))
    total = sum(int(row["claim_count"] or 0) for row in rows)
    out: dict = {}
    for i, row in enumerate(rows[:3], start=1):
        out[f"top_payer_name_{i}"] = row.get("payor_parent_name")
        count = int(row["claim_count"] or 0)
        out[f"top_payer_percent_{i}"] = round(100.0 * count / total, 2) if total else None
    return out


def fetch_group_sites(
    conn,
    organization_id: int,
    *,
    visits_total: int | None,
    wrvu_total: float | None,
    mart_db: str = MART_DB,
    max_sites: int = MAX_PRACTICE_SITES,
) -> list[ProviderPractice]:
    mart = quote_ident(mart_db)
    visits_denom = int(visits_total or 0)
    wrvu_denom = float(wrvu_total or 0)
    pr = "pr"
    dow_sql = ",\n".join(
        f"""
        ROUND(
            SUM(CASE WHEN {pr}.{col} IS NOT NULL THEN {pr}.{col} * IFNULL({pr}.visits_at_site, 0) END)
            / NULLIF(SUM(CASE WHEN {pr}.{col} IS NOT NULL THEN IFNULL({pr}.visits_at_site, 0) END), 0),
            2
        ) AS {col}
        """
        for col in SITE_DOW
    )
    sql = f"""
        SELECT
            ranked.site_rank,
            NULLIF(ranked.sl_code, 0) AS sl_code,
            ranked.cluster_key,
            NULLIF(ranked.name, '') AS name,
            NULLIF(ranked.street, '') AS street,
            NULLIF(ranked.city, '') AS city,
            NULLIF(ranked.county, '') AS county,
            NULLIF(ranked.state, '') AS state,
            NULLIF(ranked.zip, '') AS zip,
            NULLIF(ranked.latitude, '') AS latitude,
            NULLIF(ranked.longitude, '') AS longitude,
            NULLIF(ranked.phone, '') AS phone,
            NULLIF(ranked.work_type, '') AS work_type,
            ranked.visits_at_site,
            CASE WHEN %s > 0 THEN ROUND(100.0 * ranked.visits_at_site / %s, 2) END AS visit_share_pct,
            NULLIF(ranked.npi_type, '') AS npi_type,
            NULLIF(ranked.location_source, '') AS location_source,
            NULLIF(ranked.location_flag, '') AS location_flag,
            NULLIF(ranked.phone_source, '') AS phone_source,
            ranked.needs_geocode,
            ranked.wrvu_at_site,
            CASE WHEN %s > 0 THEN ROUND(100.0 * IFNULL(ranked.wrvu_at_site, 0) / %s, 2) END AS wrvu_share_pct,
            ranked.visits_percent_monday,
            ranked.visits_percent_tuesday,
            ranked.visits_percent_wednesday,
            ranked.visits_percent_thursday,
            ranked.visits_percent_friday,
            ranked.visits_percent_saturday,
            ranked.visits_percent_sunday
        FROM (
            SELECT
                g.*,
                ROW_NUMBER() OVER (
                    ORDER BY g.visits_at_site DESC, g.cluster_key
                ) AS site_rank
            FROM (
                SELECT
                    pr.cluster_key,
                    SUM(IFNULL(pr.visits_at_site, 0)) AS visits_at_site,
                    SUM(pr.wrvu_at_site) AS wrvu_at_site,
                    CAST({pick_top_sql(pr, "sl_code")} AS UNSIGNED) AS sl_code,
                    {pick_top_sql(pr, "name")} AS name,
                    {pick_top_sql(pr, "street")} AS street,
                    {pick_top_sql(pr, "city")} AS city,
                    {pick_top_sql(pr, "county")} AS county,
                    {pick_top_sql(pr, "state")} AS state,
                    {pick_top_sql(pr, "zip")} AS zip,
                    {pick_top_sql(pr, "phone")} AS phone,
                    {pick_top_sql(pr, "work_type")} AS work_type,
                    {pick_top_sql(pr, "npi_type")} AS npi_type,
                    {pick_top_sql(pr, "location_source")} AS location_source,
                    {pick_top_sql(pr, "location_flag")} AS location_flag,
                    {pick_top_sql(pr, "phone_source")} AS phone_source,
                    MAX(pr.needs_geocode) AS needs_geocode,
                    {pick_top_sql(pr, "latitude")} AS latitude,
                    {pick_top_sql(pr, "longitude")} AS longitude,
                    {dow_sql}
                FROM {mart}.pd_provider p
                INNER JOIN {mart}.pd_provider_practice pr ON pr.npi = p.npi
                WHERE p.primary_organization_id = %s
                GROUP BY pr.cluster_key
            ) g
        ) ranked
        WHERE ranked.site_rank <= %s
        ORDER BY ranked.site_rank
    """
    rows = _fetch(
        conn,
        sql,
        (visits_denom, visits_denom, wrvu_denom, wrvu_denom, organization_id, int(max_sites)),
    )
    items = []
    for row in rows:
        row["npi"] = organization_id
        _as_bool(row, "needs_geocode")
        items.append(ProviderPractice.model_validate(row))
    return items


def fetch_group_referrals(
    conn,
    organization_id: int,
    *,
    mart_db: str = MART_DB,
    max_peers: int = MAX_REFERRAL_PEERS,
) -> list[ProviderReferral]:
    mart = quote_ident(mart_db)
    sql = f"""
        SELECT
            ranked.direction,
            ranked.peer_rank,
            ranked.peer_npi,
            ranked.peer_name,
            ranked.peer_specialty,
            ranked.patient_count,
            ranked.claim_count
        FROM (
            SELECT
                g.direction,
                g.peer_npi,
                g.peer_name,
                g.peer_specialty,
                g.patient_count,
                g.claim_count,
                ROW_NUMBER() OVER (
                    PARTITION BY g.direction
                    ORDER BY g.patient_count DESC, g.peer_npi
                ) AS peer_rank
            FROM (
                SELECT
                    r.direction,
                    r.peer_npi,
                    MAX(r.peer_name) AS peer_name,
                    MAX(r.peer_specialty) AS peer_specialty,
                    SUM(IFNULL(r.patient_count, 0)) AS patient_count,
                    SUM(r.claim_count) AS claim_count
                FROM {mart}.pd_provider p
                INNER JOIN {mart}.pd_provider_referral r ON r.npi = p.npi
                WHERE p.primary_organization_id = %s
                GROUP BY r.direction, r.peer_npi
            ) g
        ) ranked
        WHERE ranked.peer_rank <= %s
        ORDER BY ranked.direction, ranked.peer_rank
    """
    rows = _fetch(conn, sql, (organization_id, int(max_peers)))
    items = []
    for row in rows:
        row["npi"] = organization_id
        items.append(ProviderReferral.model_validate(row))
    return items


def attach_group_profile(
    conn,
    row: GroupPracticeDumpRow,
    *,
    mart_db: str = MART_DB,
) -> GroupPracticeProfile:
    from provider_directory.lookup import fetch_group_hospital_affiliations

    organization_id = row.organization_id
    extras: dict = {
        "visits_are_summed_across_npis": True,
        "in_system_provider": bool(row.in_system_provider_count),
        "active_provider": bool(row.active_provider_count),
        "group_practices": [],
        "utilization": [],
    }
    extras.update(fetch_group_metrics(conn, organization_id, mart_db=mart_db))
    wrvu_n = extras.get("wrvu_procedure_count")
    if wrvu_n and row.wrvu_total is not None:
        extras["wrvu_average"] = round(float(row.wrvu_total) / int(wrvu_n), 3)
    prior = extras.get("wrvu_prior_year_total")
    if row.wrvu_total is not None and prior not in (None, 0):
        extras["wrvu_yoy_change_pct"] = round(
            100.0 * (float(row.wrvu_total) - float(prior)) / float(prior), 2
        )
    extras.update(fetch_group_modal_specialty(conn, organization_id, mart_db=mart_db))
    extras.update(
        fetch_group_top_codes(
            conn,
            organization_id,
            table="pd_stg_top_dx",
            prefix="visits_top_diagnosis",
            visits_total=row.visits_total,
            mart_db=mart_db,
        )
    )
    extras.update(
        fetch_group_top_codes(
            conn,
            organization_id,
            table="pd_stg_top_px",
            prefix="visits_top_procedure",
            visits_total=row.visits_total,
            mart_db=mart_db,
        )
    )
    extras.update(fetch_group_top_payers(conn, organization_id, mart_db=mart_db))
    extras["practices"] = fetch_group_sites(
        conn,
        organization_id,
        visits_total=row.visits_total,
        wrvu_total=row.wrvu_total,
        mart_db=mart_db,
    )
    extras["referrals"] = fetch_group_referrals(conn, organization_id, mart_db=mart_db)
    extras["hospital_affiliations"] = fetch_group_hospital_affiliations(
        conn,
        organization_id,
        visits_total=row.visits_total,
        mart_db=mart_db,
    )
    payload = {k: v for k, v in extras.items() if k in GroupPracticeProfile.model_fields}
    return GroupPracticeProfile(**row.model_dump(), **payload)
