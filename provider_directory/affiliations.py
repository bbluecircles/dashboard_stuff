"""Phase 4 lists: every in-window group practice, top-in hospital systems.

Reads affiliation P/I and sl from the claims DB. Writes mart tables only.
Does not rescan pat_dt. Sites (street+ZIP) stay on pd_provider_practice.
"""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.locations import (
    hospital_facility_name_sql,
    hospital_system_name_sql,
    is_person_practice_name_sql,
    table_has_rows,
)
from provider_directory.schema import table_options
from provider_directory.settings import (
    CLAIMS_DB,
    DUMMY_NPIS,
    MART_DB,
    MAX_HOSPITAL_AFFILIATIONS,
    MIN_HOSPITAL_AFFILIATION_SHARE_PCT,
    WINDOW_START,
)

PROVIDER_BUCKETS = 16


def _session_timeouts(cur) -> None:
    cur.execute("SET SESSION wait_timeout = 28800")
    cur.execute("SET SESSION net_read_timeout = 28800")
    cur.execute("SET SESSION net_write_timeout = 28800")
    try:
        cur.execute("SET SESSION max_statement_time = 0")
    except Exception:
        pass


def _run(cur, conn, sql: str, params: tuple | None = None) -> int:
    cur.execute(sql, params or ())
    n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    conn.commit()
    return n


def _truncate_if_exists(cur, conn, mart_db: str, table: str) -> None:
    mart = quote_ident(mart_db)
    cur.execute(
        """
        SELECT 1 AS ok
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        """,
        (mart_db, table),
    )
    if cur.fetchone():
        cur.execute(f"TRUNCATE TABLE {mart}.{quote_ident(table)}")
        conn.commit()


def _affiliation_name_ok_sql(alias: str) -> str:
    name = f"{alias}.dba_name"
    return f"""
        NULLIF(TRIM({name}), '') IS NOT NULL
        AND UPPER(TRIM({name})) NOT IN ('UNKNOWN GROUP PRACTICE', 'UNKNOWN')
        AND NOT (
            {alias}.provider_billing_code = {alias}.rendering_physician_code
            AND {is_person_practice_name_sql(name)}
        )
    """


def rebuild_org_lists(
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    window_start: int = WINDOW_START,
    max_systems: int = MAX_HOSPITAL_AFFILIATIONS,
    min_share_pct: float = MIN_HOSPITAL_AFFILIATION_SHARE_PCT,
) -> dict:
    """Fill nested group-practice and hospital-affiliation lists.

    Group practices: physician_affiliation_p then _i in/after the frozen
    window, plus the header primary org if it is missing. Rank primary first.

    Hospital affiliations: visit-weighted SL collapsed to distinct
    sl_hospital_system_name (not campuses). Top `max_systems` with share
    >= min_share_pct.
    """
    mart = quote_ident(mart_db)
    claims = quote_ident(claims_db)
    dummy = ", ".join(str(n) for n in sorted(DUMMY_NPIS))
    start = int(window_start)
    max_systems = int(max_systems)
    min_share = float(min_share_pct)
    has_npi_sl = table_has_rows(conn, mart_db, "pd_stg_npi_sl")
    counts: dict[str, int] = {
        "group_practice_rows": 0,
        "hospital_affiliation_rows": 0,
    }

    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()
        _truncate_if_exists(cur, conn, mart_db, "pd_provider_group_practice")
        _truncate_if_exists(cur, conn, mart_db, "pd_provider_hospital_affiliation")
        cur.execute("DROP TEMPORARY TABLE IF EXISTS tmp_npi_org")
        conn.commit()

        cur.execute(
            f"""
            CREATE TEMPORARY TABLE tmp_npi_org (
                npi BIGINT UNSIGNED NOT NULL,
                organization_id BIGINT NOT NULL,
                organization_name VARCHAR(180),
                billing_type CHAR(1) NULL,
                rollup_priority INT NULL,
                cases INT UNSIGNED NULL,
                max_period_code INT UNSIGNED NULL,
                KEY idx_npi_org (npi, organization_id)
            ) {table_options()}
            """
        )
        conn.commit()

        name_ok = _affiliation_name_ok_sql("a")
        p_sql = f"""
            INSERT INTO tmp_npi_org (
                npi, organization_id, organization_name, billing_type,
                rollup_priority, cases, max_period_code
            )
            SELECT
                a.rendering_physician_code,
                a.provider_billing_code,
                LEFT(TRIM(a.dba_name), 180),
                LEFT(TRIM(a.CLAIM_TYP_CD), 1),
                a.rollup_priority,
                a.count_of_cases,
                a.max_period_code
            FROM {claims}.physician_affiliation_p a
            INNER JOIN {mart}.pd_provider p ON p.npi = a.rendering_physician_code
            WHERE MOD(a.rendering_physician_code, {PROVIDER_BUCKETS}) = %s
              AND a.provider_billing_code NOT IN ({dummy})
              AND a.provider_billing_code IS NOT NULL
              AND a.max_period_code >= {start}
              AND {name_ok}
        """
        i_sql = f"""
            INSERT INTO tmp_npi_org (
                npi, organization_id, organization_name, billing_type,
                rollup_priority, cases, max_period_code
            )
            SELECT
                a.rendering_physician_code,
                a.provider_billing_code,
                LEFT(TRIM(a.dba_name), 180),
                LEFT(TRIM(a.CLAIM_TYP_CD), 1),
                a.rollup_priority,
                a.count_of_cases,
                a.max_period_code
            FROM {claims}.physician_affiliation_i a
            INNER JOIN {mart}.pd_provider p ON p.npi = a.rendering_physician_code
            WHERE MOD(a.rendering_physician_code, {PROVIDER_BUCKETS}) = %s
              AND a.provider_billing_code NOT IN ({dummy})
              AND a.provider_billing_code IS NOT NULL
              AND a.max_period_code >= {start}
              AND {name_ok}
        """
        header_sql = f"""
            INSERT INTO tmp_npi_org (
                npi, organization_id, organization_name, billing_type,
                rollup_priority, cases, max_period_code
            )
            SELECT
                p.npi,
                p.primary_organization_id,
                LEFT(TRIM(p.primary_organization_name), 180),
                NULL,
                0,
                NULL,
                NULL
            FROM {mart}.pd_provider p
            WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
              AND p.primary_organization_id IS NOT NULL
              AND p.primary_organization_id NOT IN ({dummy})
              AND NULLIF(TRIM(p.primary_organization_name), '') IS NOT NULL
        """
        rank_sql = f"""
            INSERT INTO {mart}.pd_provider_group_practice (
                npi, org_rank, organization_id, organization_npi, organization_name,
                billing_type, is_primary, cases, max_period_code, refreshed_at
            )
            SELECT
                ranked.npi,
                ranked.org_rank,
                ranked.organization_id,
                ranked.organization_id,
                ranked.organization_name,
                ranked.billing_type,
                ranked.is_primary,
                ranked.cases,
                ranked.max_period_code,
                NOW()
            FROM (
                SELECT
                    picked.npi,
                    picked.organization_id,
                    picked.organization_name,
                    picked.billing_type,
                    picked.cases,
                    picked.max_period_code,
                    picked.is_primary,
                    ROW_NUMBER() OVER (
                        PARTITION BY picked.npi
                        ORDER BY
                            CASE WHEN picked.is_primary = 1 THEN 0 ELSE 1 END,
                            CASE WHEN picked.rollup_priority = 1 THEN 0 ELSE 1 END,
                            IFNULL(picked.rollup_priority, 99),
                            IFNULL(picked.cases, 0) DESC,
                            IFNULL(picked.max_period_code, 0) DESC,
                            picked.organization_id
                    ) AS org_rank
                FROM (
                    SELECT
                        t.npi,
                        t.organization_id,
                        t.organization_name,
                        t.billing_type,
                        t.rollup_priority,
                        t.cases,
                        t.max_period_code,
                        CASE
                            WHEN t.organization_id = p.primary_organization_id THEN 1
                            ELSE 0
                        END AS is_primary,
                        ROW_NUMBER() OVER (
                            PARTITION BY t.npi, t.organization_id
                            ORDER BY
                                CASE WHEN t.billing_type IS NULL THEN 1 ELSE 0 END,
                                CASE t.billing_type WHEN 'P' THEN 0 ELSE 1 END,
                                IFNULL(t.rollup_priority, 99),
                                IFNULL(t.cases, 0) DESC,
                                IFNULL(t.max_period_code, 0) DESC
                        ) AS dedupe_rk
                    FROM tmp_npi_org t
                    INNER JOIN {mart}.pd_provider p ON p.npi = t.npi
                    WHERE MOD(t.npi, {PROVIDER_BUCKETS}) = %s
                ) picked
                WHERE picked.dedupe_rk = 1
            ) ranked
        """
        for bucket in range(PROVIDER_BUCKETS):
            _run(cur, conn, "TRUNCATE TABLE tmp_npi_org")
            _run(cur, conn, p_sql, (bucket,))
            _run(cur, conn, i_sql, (bucket,))
            _run(cur, conn, header_sql, (bucket,))
            n = _run(cur, conn, rank_sql, (bucket,))
            counts["group_practice_rows"] += n
            print(f"phase4 group practices bucket {bucket}: {n} rows", flush=True)

        if not has_npi_sl:
            return counts

        system_sql = hospital_system_name_sql()
        facility_sql = hospital_facility_name_sql()
        sep = "CHAR(31)"
        hospital_sql = f"""
            INSERT INTO {mart}.pd_provider_hospital_affiliation (
                npi, affiliation_rank, hospital_system_name, facility_name, sl_code,
                visits_at_system, visit_share_pct, refreshed_at
            )
            SELECT
                ranked.npi,
                ranked.affiliation_rank,
                ranked.hospital_system_name,
                CASE
                    WHEN NULLIF(TRIM(ranked.facility_name), '') IS NULL THEN NULL
                    WHEN UPPER(TRIM(ranked.facility_name))
                         = UPPER(TRIM(ranked.hospital_system_name)) THEN NULL
                    ELSE LEFT(TRIM(ranked.facility_name), 180)
                END,
                ranked.sl_code,
                ranked.visits_at_system,
                ROUND(100.0 * ranked.visits_at_system / NULLIF(p.visits_total, 0), 2),
                NOW()
            FROM (
                SELECT
                    g.npi,
                    g.hospital_system_name,
                    g.facility_name,
                    g.sl_code,
                    g.visits_at_system,
                    ROW_NUMBER() OVER (
                        PARTITION BY g.npi
                        ORDER BY g.visits_at_system DESC, g.hospital_system_name
                    ) AS affiliation_rank
                FROM (
                    SELECT
                        x.npi,
                        SUBSTRING_INDEX(
                            MAX(CONCAT(LPAD(x.visits, 10, '0'), {sep}, x.hospital_system_name)),
                            {sep}, -1
                        ) AS hospital_system_name,
                        SUBSTRING_INDEX(
                            MAX(CONCAT(LPAD(x.visits, 10, '0'), {sep}, IFNULL(x.facility_name, ''))),
                            {sep}, -1
                        ) AS facility_name,
                        CAST(SUBSTRING_INDEX(
                            MAX(CONCAT(LPAD(x.visits, 10, '0'), {sep}, x.sl_code)),
                            {sep}, -1
                        ) AS UNSIGNED) AS sl_code,
                        SUM(x.visits) AS visits_at_system
                    FROM (
                        SELECT
                            s.npi,
                            s.sl_code,
                            s.visits,
                            {system_sql} AS hospital_system_name,
                            {facility_sql} AS facility_name
                        FROM {mart}.pd_stg_npi_sl s
                        INNER JOIN {claims}.sl sl ON sl.sl_code = s.sl_code
                        LEFT JOIN {claims}.provider_facility_npi fac
                            ON fac.PROVIDER_FACILITY_NPI_code = sl.sl_code
                        WHERE MOD(s.npi, {PROVIDER_BUCKETS}) = %s
                    ) x
                    WHERE x.hospital_system_name IS NOT NULL
                    GROUP BY x.npi, UPPER(x.hospital_system_name)
                ) g
            ) ranked
            INNER JOIN {mart}.pd_provider p ON p.npi = ranked.npi
            WHERE ranked.affiliation_rank <= {max_systems}
              AND IFNULL(p.visits_total, 0) > 0
              AND (100.0 * ranked.visits_at_system / p.visits_total) >= {min_share}
        """
        for bucket in range(PROVIDER_BUCKETS):
            n = _run(cur, conn, hospital_sql, (bucket,))
            counts["hospital_affiliation_rows"] += n
            print(f"phase4 hospital affiliations bucket {bucket}: {n} rows", flush=True)

    return counts
