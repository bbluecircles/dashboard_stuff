"""Phase 2: activity, visits, panel, top dx/px. Reads az/azal, writes az_pd only."""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.schema import create_schema
from provider_directory.settings import (
    CLAIMS_DB,
    DUMMY_NPIS,
    LOOKUP_DB,
    MART_DB,
    WINDOW_END,
    WINDOW_START,
)


def _session_timeouts(cur) -> None:
    cur.execute("SET SESSION wait_timeout = 28800")
    cur.execute("SET SESSION net_read_timeout = 28800")
    cur.execute("SET SESSION net_write_timeout = 28800")
    try:
        cur.execute("SET SESSION max_statement_time = 0")
    except Exception:
        pass


def reset_activity_columns_sql(mart_db: str = MART_DB) -> str:
    mart = quote_ident(mart_db)
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
    """One claims-window scan into az_pd staging, then roll up onto pd_provider."""
    create_schema(conn, mart_db)
    mart = quote_ident(mart_db)
    claims = quote_ident(claims_db)
    lookup = quote_ident(lookup_db)
    dummy = ", ".join(str(n) for n in sorted(DUMMY_NPIS))
    counts: dict[str, int] = {}

    with conn.cursor() as cur:
        _session_timeouts(cur)
        for table in (
            "pd_stg_window_claim",
            "pd_stg_visit",
            "pd_stg_panel_patient",
            "pd_stg_top_dx",
            "pd_stg_top_px",
        ):
            cur.execute(f"TRUNCATE TABLE {mart}.{quote_ident(table)}")
        cur.execute(reset_activity_columns_sql(mart_db))
        conn.commit()

        cur.execute(
            f"""
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
            WHERE period_code BETWEEN %s AND %s
            """,
            (window_start, window_end),
        )
        counts["window_claims"] = cur.rowcount
        conn.commit()

        cur.execute(
            f"""
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
            GROUP BY t.encounter_id
            """
        )
        counts["visits"] = cur.rowcount
        conn.commit()

        cur.execute(
            f"""
            INSERT INTO {mart}.pd_stg_panel_patient (npi, pat_id, age_code, gender_code)
            SELECT npi, pat_id, MAX(age_code), MAX(gender_code)
            FROM (
                SELECT c.rendering_physician_code AS npi, c.pat_id, c.age_code, c.gender_code
                FROM {mart}.pd_stg_window_claim c
                INNER JOIN {mart}.pd_provider p ON p.npi = c.rendering_physician_code
                WHERE c.pat_id IS NOT NULL
                UNION ALL
                SELECT c.referring_physician_code AS npi, c.pat_id, c.age_code, c.gender_code
                FROM {mart}.pd_stg_window_claim c
                INNER JOIN {mart}.pd_provider p ON p.npi = c.referring_physician_code
                WHERE c.pat_id IS NOT NULL
                  AND c.referring_physician_code NOT IN ({dummy})
            ) src
            GROUP BY npi, pat_id
            """
        )
        counts["panel_patients"] = cur.rowcount
        conn.commit()

        cur.execute(
            f"""
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
                GROUP BY v.rendering_npi, v.dx
            ) ranked
            LEFT JOIN {lookup}.diagnosis d ON d.diagnosis_code = ranked.code
            WHERE ranked.rk <= 3
            """
        )
        counts["top_dx_rows"] = cur.rowcount

        cur.execute(
            f"""
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
                GROUP BY v.rendering_npi, v.px
            ) ranked
            LEFT JOIN {lookup}.procd pr ON pr.procd_code = ranked.code
            WHERE ranked.rk <= 3
            """
        )
        counts["top_px_rows"] = cur.rowcount
        conn.commit()

        cur.execute(
            f"""
            UPDATE {mart}.pd_provider p
            LEFT JOIN (
                SELECT rendering_npi AS npi, COUNT(*) AS visits_total
                FROM {mart}.pd_stg_visit
                GROUP BY rendering_npi
            ) v ON v.npi = p.npi
            LEFT JOIN (
                SELECT
                    npi,
                    COUNT(*) AS panel_size,
                    ROUND(AVG(CASE WHEN age_code BETWEEN 0 AND 120 THEN age_code END), 1) AS panel_average_age,
                    ROUND(100.0 * SUM(CASE WHEN age_code BETWEEN 0 AND 19 THEN 1 ELSE 0 END) / COUNT(*), 2)
                        AS panel_percent_age_0_19,
                    ROUND(100.0 * SUM(CASE WHEN age_code BETWEEN 20 AND 44 THEN 1 ELSE 0 END) / COUNT(*), 2)
                        AS panel_percent_age_20_44,
                    ROUND(100.0 * SUM(CASE WHEN age_code BETWEEN 45 AND 64 THEN 1 ELSE 0 END) / COUNT(*), 2)
                        AS panel_percent_age_45_64,
                    ROUND(100.0 * SUM(CASE WHEN age_code BETWEEN 65 AND 84 THEN 1 ELSE 0 END) / COUNT(*), 2)
                        AS panel_percent_age_65_84,
                    ROUND(100.0 * SUM(CASE WHEN age_code BETWEEN 85 AND 120 THEN 1 ELSE 0 END) / COUNT(*), 2)
                        AS panel_percent_age_85_plus,
                    ROUND(100.0 * SUM(CASE WHEN UPPER(gender_code) IN ('F', 'FEMALE') THEN 1 ELSE 0 END) / COUNT(*), 2)
                        AS panel_percent_female,
                    ROUND(100.0 * SUM(CASE WHEN UPPER(gender_code) IN ('M', 'MALE') THEN 1 ELSE 0 END) / COUNT(*), 2)
                        AS panel_percent_male
                FROM {mart}.pd_stg_panel_patient
                GROUP BY npi
            ) pan ON pan.npi = p.npi
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
            """
        )
        counts["providers_updated"] = cur.rowcount
        conn.commit()

    return {
        "window_start": window_start,
        "window_end": window_end,
        **counts,
    }
