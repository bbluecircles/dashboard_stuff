"""Phase 2: activity, visits, panel, top dx/px. Reads az/azal, writes az_pd only.

Galera (and similar) reject a single huge transaction ("Maximum writeset size
exceeded"). Every INSERT/UPDATE here commits in monthly or hashed slices.
"""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.schema import create_schema, drop_staging_tables, table_options
from provider_directory.settings import (
    CLAIMS_DB,
    DUMMY_NPIS,
    LOOKUP_DB,
    MART_COLLATION,
    MART_DB,
    WINDOW_END,
    WINDOW_START,
)

# Keep each writeset well under typical wsrep_max_ws_size (~2GB).
CLAIM_ID_BUCKETS = 4
VISIT_BUCKETS = 16
PROVIDER_BUCKETS = 16


def iter_period_codes(start: int, end: int) -> list[int]:
    """Inclusive YYYYMM months from start through end."""
    periods: list[int] = []
    year, month = divmod(start, 100)
    current = start
    while current <= end:
        periods.append(current)
        month += 1
        if month > 12:
            month = 1
            year += 1
        current = year * 100 + month
    return periods


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


def reset_activity_columns_sql(mart_db: str = MART_DB, bucket: int | None = None, buckets: int = PROVIDER_BUCKETS) -> str:
    mart = quote_ident(mart_db)
    where = ""
    if bucket is not None:
        where = f" WHERE MOD(npi, {int(buckets)}) = {int(bucket)}"
    return f"""
        UPDATE {mart}.pd_provider
        SET
            active_provider = 0,
            visits_total = 0,
            visits_top_diagnosis_1 = NULL,
            visits_top_diagnosis_1_name = NULL,
            visits_top_diagnosis_2 = NULL,
            visits_top_diagnosis_2_name = NULL,
            visits_top_diagnosis_3 = NULL,
            visits_top_diagnosis_3_name = NULL,
            visits_top_procedure_1 = NULL,
            visits_top_procedure_1_name = NULL,
            visits_top_procedure_2 = NULL,
            visits_top_procedure_2_name = NULL,
            visits_top_procedure_3 = NULL,
            visits_top_procedure_3_name = NULL,
            panel_size = 0,
            panel_average_age = NULL,
            panel_percent_age_0_19 = NULL,
            panel_percent_age_20_44 = NULL,
            panel_percent_age_45_64 = NULL,
            panel_percent_age_65_84 = NULL,
            panel_percent_age_85_plus = NULL,
            panel_percent_female = NULL,
            panel_percent_male = NULL
        {where}
    """


def rebuild_activity(
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    lookup_db: str = LOOKUP_DB,
    window_start: int = WINDOW_START,
    window_end: int = WINDOW_END,
) -> dict:
    """Scan the frozen claims window into az_pd in Galera-safe chunks, then roll up."""
    drop_staging_tables(conn, mart_db)
    create_schema(conn, mart_db)
    mart = quote_ident(mart_db)
    claims = quote_ident(claims_db)
    lookup = quote_ident(lookup_db)
    dummy = ", ".join(str(n) for n in sorted(DUMMY_NPIS))
    periods = iter_period_codes(window_start, window_end)
    counts: dict[str, int] = {
        "window_claims": 0,
        "visits": 0,
        "panel_patients": 0,
        "top_dx_rows": 0,
        "top_px_rows": 0,
        "providers_updated": 0,
    }

    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()
        for bucket in range(PROVIDER_BUCKETS):
            _run(cur, conn, reset_activity_columns_sql(mart_db, bucket=bucket))

        window_sql = f"""
            INSERT INTO {mart}.pd_stg_window_claim (
                encounter_id, period_code, pat_id, age_code, gender_code,
                rendering_physician_code, referring_physician_code,
                encounter_rendering_physician_code,
                encounter_diagnosis_code, encounter_work_procd_code, sl_code
            )
            SELECT
                encounter_id, period_code, pat_id, age_code, gender_code,
                rendering_physician_code, referring_physician_code,
                encounter_rendering_physician_code,
                NULLIF(TRIM(encounter_diagnosis_code), ''),
                NULLIF(TRIM(encounter_work_procd_code), ''),
                sl_code
            FROM {claims}.pat_dt
            WHERE period_code = %s
              AND MOD(IFNULL(pat_id, 0), {CLAIM_ID_BUCKETS}) = %s
        """
        for period in periods:
            for bucket in range(CLAIM_ID_BUCKETS):
                n = _run(cur, conn, window_sql, (period, bucket))
                counts["window_claims"] += n
                print(f"phase2 window {period} bucket {bucket}: {n} rows", flush=True)

        visit_sql = f"""
            INSERT INTO {mart}.pd_stg_visit (
                encounter_id, rendering_npi, dx, px, pat_id, period_code
            )
            SELECT
                t.encounter_id,
                MAX(t.encounter_rendering_physician_code),
                MAX(t.encounter_diagnosis_code),
                MAX(t.encounter_work_procd_code),
                MIN(t.pat_id),
                MIN(t.period_code)
            FROM {mart}.pd_stg_window_claim t
            WHERE t.encounter_id IS NOT NULL AND t.encounter_id <> 0
              AND MOD(t.encounter_id, {VISIT_BUCKETS}) = %s
            GROUP BY t.encounter_id
        """
        for bucket in range(VISIT_BUCKETS):
            n = _run(cur, conn, visit_sql, (bucket,))
            counts["visits"] += n
            print(f"phase2 visits bucket {bucket}: {n} rows", flush=True)

        panel_sql = f"""
            INSERT INTO {mart}.pd_stg_panel_patient (npi, pat_id, age_code, gender_code)
            SELECT npi, pat_id, MAX(age_code), MAX(gender_code)
            FROM (
                SELECT c.rendering_physician_code AS npi, c.pat_id, c.age_code, c.gender_code
                FROM {mart}.pd_stg_window_claim c
                INNER JOIN {mart}.pd_provider p ON p.npi = c.rendering_physician_code
                WHERE c.pat_id IS NOT NULL AND c.period_code = %s
                UNION ALL
                SELECT c.referring_physician_code AS npi, c.pat_id, c.age_code, c.gender_code
                FROM {mart}.pd_stg_window_claim c
                INNER JOIN {mart}.pd_provider p ON p.npi = c.referring_physician_code
                WHERE c.pat_id IS NOT NULL
                  AND c.period_code = %s
                  AND c.referring_physician_code NOT IN ({dummy})
            ) src
            GROUP BY npi, pat_id
            ON DUPLICATE KEY UPDATE
                age_code = GREATEST(IFNULL({mart}.pd_stg_panel_patient.age_code, 0), VALUES(age_code)),
                gender_code = COALESCE(VALUES(gender_code), {mart}.pd_stg_panel_patient.gender_code)
        """
        for period in periods:
            n = _run(cur, conn, panel_sql, (period, period))
            counts["panel_patients"] += n
            print(f"phase2 panel {period}: {n} rows", flush=True)

        top_dx_sql = f"""
            INSERT INTO {mart}.pd_stg_top_dx (npi, code, name, visit_count, rk)
            SELECT ranked.npi, ranked.code,
                   LEFT(d.diagnosis_name, 80),
                   ranked.visit_count, ranked.rk
            FROM (
                SELECT
                    v.rendering_npi AS npi,
                    v.dx AS code,
                    COUNT(*) AS visit_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY v.rendering_npi
                        ORDER BY COUNT(*) DESC, v.dx
                    ) AS rk
                FROM {mart}.pd_stg_visit v
                INNER JOIN {mart}.pd_provider p ON p.npi = v.rendering_npi
                WHERE v.dx IS NOT NULL AND v.dx <> ''
                  AND MOD(v.rendering_npi, {PROVIDER_BUCKETS}) = %s
                GROUP BY v.rendering_npi, v.dx
            ) ranked
            LEFT JOIN {lookup}.diagnosis d
                ON d.diagnosis_code = ranked.code COLLATE {MART_COLLATION}
            WHERE ranked.rk <= 3
        """
        top_px_sql = f"""
            INSERT INTO {mart}.pd_stg_top_px (npi, code, name, visit_count, rk)
            SELECT ranked.npi, ranked.code, LEFT(pr.procd_name, 80), ranked.visit_count, ranked.rk
            FROM (
                SELECT
                    v.rendering_npi AS npi,
                    v.px AS code,
                    COUNT(*) AS visit_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY v.rendering_npi
                        ORDER BY COUNT(*) DESC, v.px
                    ) AS rk
                FROM {mart}.pd_stg_visit v
                INNER JOIN {mart}.pd_provider p ON p.npi = v.rendering_npi
                WHERE v.px IS NOT NULL AND v.px <> ''
                  AND MOD(v.rendering_npi, {PROVIDER_BUCKETS}) = %s
                GROUP BY v.rendering_npi, v.px
            ) ranked
            LEFT JOIN {lookup}.procd pr
                ON pr.procd_code = ranked.code COLLATE {MART_COLLATION}
            WHERE ranked.rk <= 3
        """
        for bucket in range(PROVIDER_BUCKETS):
            counts["top_dx_rows"] += _run(cur, conn, top_dx_sql, (bucket,))
            counts["top_px_rows"] += _run(cur, conn, top_px_sql, (bucket,))
            print(f"phase2 top codes bucket {bucket}", flush=True)

        cur.execute(
            f"""
            CREATE TEMPORARY TABLE tmp_visit_counts (
                npi BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                visits_total INT UNSIGNED NOT NULL
            ) {table_options()}
            """
        )
        counts["visit_npi"] = _run(
            cur,
            conn,
            f"""
            INSERT INTO tmp_visit_counts (npi, visits_total)
            SELECT rendering_npi, COUNT(*)
            FROM {mart}.pd_stg_visit
            GROUP BY rendering_npi
            """,
        )
        cur.execute(
            f"""
            CREATE TEMPORARY TABLE tmp_panel_counts (
                npi BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                panel_size INT UNSIGNED NOT NULL,
                panel_average_age DECIMAL(5,1),
                panel_percent_age_0_19 DECIMAL(6,2),
                panel_percent_age_20_44 DECIMAL(6,2),
                panel_percent_age_45_64 DECIMAL(6,2),
                panel_percent_age_65_84 DECIMAL(6,2),
                panel_percent_age_85_plus DECIMAL(6,2),
                panel_percent_female DECIMAL(6,2),
                panel_percent_male DECIMAL(6,2)
            ) {table_options()}
            """
        )
        counts["panel_npi"] = _run(
            cur,
            conn,
            f"""
            INSERT INTO tmp_panel_counts
            SELECT
                npi,
                COUNT(*),
                ROUND(AVG(CASE WHEN age_code BETWEEN 0 AND 120 THEN age_code END), 1),
                ROUND(100.0 * SUM(CASE WHEN age_code BETWEEN 0 AND 19 THEN 1 ELSE 0 END) / COUNT(*), 2),
                ROUND(100.0 * SUM(CASE WHEN age_code BETWEEN 20 AND 44 THEN 1 ELSE 0 END) / COUNT(*), 2),
                ROUND(100.0 * SUM(CASE WHEN age_code BETWEEN 45 AND 64 THEN 1 ELSE 0 END) / COUNT(*), 2),
                ROUND(100.0 * SUM(CASE WHEN age_code BETWEEN 65 AND 84 THEN 1 ELSE 0 END) / COUNT(*), 2),
                ROUND(100.0 * SUM(CASE WHEN age_code BETWEEN 85 AND 120 THEN 1 ELSE 0 END) / COUNT(*), 2),
                ROUND(100.0 * SUM(CASE WHEN UPPER(gender_code) IN ('F', 'FEMALE') THEN 1 ELSE 0 END) / COUNT(*), 2),
                ROUND(100.0 * SUM(CASE WHEN UPPER(gender_code) IN ('M', 'MALE') THEN 1 ELSE 0 END) / COUNT(*), 2)
            FROM {mart}.pd_stg_panel_patient
            GROUP BY npi
            """,
        )
        print("phase2 rollup tables ready", flush=True)

        overlay_sql = f"""
            UPDATE {mart}.pd_provider p
            LEFT JOIN tmp_visit_counts v ON v.npi = p.npi
            LEFT JOIN tmp_panel_counts pan ON pan.npi = p.npi
            LEFT JOIN (
                SELECT
                    npi,
                    MAX(CASE WHEN rk = 1 THEN code END) AS d1,
                    MAX(CASE WHEN rk = 1 THEN name END) AS d1_name,
                    MAX(CASE WHEN rk = 2 THEN code END) AS d2,
                    MAX(CASE WHEN rk = 2 THEN name END) AS d2_name,
                    MAX(CASE WHEN rk = 3 THEN code END) AS d3,
                    MAX(CASE WHEN rk = 3 THEN name END) AS d3_name
                FROM {mart}.pd_stg_top_dx
                GROUP BY npi
            ) dx ON dx.npi = p.npi
            LEFT JOIN (
                SELECT
                    npi,
                    MAX(CASE WHEN rk = 1 THEN code END) AS p1,
                    MAX(CASE WHEN rk = 1 THEN name END) AS p1_name,
                    MAX(CASE WHEN rk = 2 THEN code END) AS p2,
                    MAX(CASE WHEN rk = 2 THEN name END) AS p2_name,
                    MAX(CASE WHEN rk = 3 THEN code END) AS p3,
                    MAX(CASE WHEN rk = 3 THEN name END) AS p3_name
                FROM {mart}.pd_stg_top_px
                GROUP BY npi
            ) px ON px.npi = p.npi
            SET
                p.visits_total = IFNULL(v.visits_total, 0),
                p.panel_size = IFNULL(pan.panel_size, 0),
                p.active_provider = IF(IFNULL(v.visits_total, 0) > 0 OR IFNULL(pan.panel_size, 0) > 0, 1, 0),
                p.panel_average_age = pan.panel_average_age,
                p.panel_percent_age_0_19 = pan.panel_percent_age_0_19,
                p.panel_percent_age_20_44 = pan.panel_percent_age_20_44,
                p.panel_percent_age_45_64 = pan.panel_percent_age_45_64,
                p.panel_percent_age_65_84 = pan.panel_percent_age_65_84,
                p.panel_percent_age_85_plus = pan.panel_percent_age_85_plus,
                p.panel_percent_female = pan.panel_percent_female,
                p.panel_percent_male = pan.panel_percent_male,
                p.visits_top_diagnosis_1 = dx.d1,
                p.visits_top_diagnosis_1_name = dx.d1_name,
                p.visits_top_diagnosis_2 = dx.d2,
                p.visits_top_diagnosis_2_name = dx.d2_name,
                p.visits_top_diagnosis_3 = dx.d3,
                p.visits_top_diagnosis_3_name = dx.d3_name,
                p.visits_top_procedure_1 = px.p1,
                p.visits_top_procedure_1_name = px.p1_name,
                p.visits_top_procedure_2 = px.p2,
                p.visits_top_procedure_2_name = px.p2_name,
                p.visits_top_procedure_3 = px.p3,
                p.visits_top_procedure_3_name = px.p3_name,
                p.refreshed_at = NOW()
            WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
        """
        for bucket in range(PROVIDER_BUCKETS):
            n = _run(cur, conn, overlay_sql, (bucket,))
            counts["providers_updated"] += n
            print(f"phase2 overlay bucket {bucket}: {n} rows", flush=True)

    return {
        "window_start": window_start,
        "window_end": window_end,
        "periods": periods,
        **counts,
    }
