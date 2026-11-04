"""Phase 2: activity, visits, panel, top dx/px. Reads az/azal, writes az_pd only.

Galera (and similar) reject a single huge transaction ("Maximum writeset size
exceeded"). Every INSERT/UPDATE here commits in monthly or hashed slices.
"""

from __future__ import annotations

from provider_directory.db import quote_ident
from provider_directory.group_summary import rebuild_group_summary
from provider_directory.schema import create_schema, drop_phase2_derived, drop_staging_tables, table_options
from provider_directory.settings import (
    CLAIMS_DB,
    DUMMY_NPIS,
    LOOKUP_DB,
    MART_COLLATION,
    MART_DB,
    TOP_CODE_KINDS,
    TOP_N,
    WINDOW_END,
    WINDOW_START,
)
from provider_directory.transforms import primary_procedure_filter_sql
from provider_directory.window import iter_period_codes

# Keep each writeset well under typical wsrep_max_ws_size (~2GB).
CLAIM_ID_BUCKETS = 4
VISIT_BUCKETS = 16
PROVIDER_BUCKETS = 16
CLAIM_DELETE_BUCKETS = 16


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


def top_code_columns() -> list[str]:
    cols: list[str] = []
    for prefix, _table in TOP_CODE_KINDS:
        for i in range(1, TOP_N + 1):
            cols += [f"{prefix}_{i}", f"{prefix}_{i}_name"]
    return cols


def top_code_null_sql() -> str:
    return ",\n            ".join(f"{c} = NULL" for c in top_code_columns()) + ","


def top_code_overlay_sql(mart: str) -> tuple[str, str]:
    """(LEFT JOIN clauses, SET assignments) that copy rank 1..TOP_N of every
    pd_stg_top_* table onto pd_provider. Shared by the Phase 2 overlay and
    --top-codes-only."""
    joins: list[str] = []
    sets: list[str] = []
    for prefix, table in TOP_CODE_KINDS:
        alias = table.replace("pd_stg_top_", "t_")
        picks = ",\n                ".join(
            f"MAX(CASE WHEN rk = {i} THEN code END) AS c{i},\n"
            f"                MAX(CASE WHEN rk = {i} THEN name END) AS c{i}_name"
            for i in range(1, TOP_N + 1)
        )
        joins.append(f"""
        LEFT JOIN (
            SELECT
                npi,
                {picks}
            FROM {mart}.{table}
            GROUP BY npi
        ) {alias} ON {alias}.npi = p.npi""")
        for i in range(1, TOP_N + 1):
            sets.append(f"p.{prefix}_{i} = {alias}.c{i}")
            sets.append(f"p.{prefix}_{i}_name = {alias}.c{i}_name")
    return "".join(joins), ",\n            ".join(sets)


def top_code_statements(mart: str, lookup: str) -> dict[str, str]:
    """INSERT ... SELECT per ranked list, one %s param: the NPI bucket.

    Diagnoses and procedures rank every code on the visit. Primary procedures
    join the fee schedule before ranking and keep only codes whose strict
    physician work RVU is above the floor, excluding the visit-type code
    families in settings.PRIMARY_PROCEDURE_EXCLUDED_* plus Category II, C and
    U codes, so ranks 1..TOP_N are the top eligible procedures.
    """
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
        WHERE ranked.rk <= {TOP_N}
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
        WHERE ranked.rk <= {TOP_N}
    """
    top_primary_px_sql = f"""
        INSERT INTO {mart}.pd_stg_top_primary_px (npi, code, name, visit_count, rk)
        SELECT ranked.npi, ranked.code, ranked.name, ranked.visit_count, ranked.rk
        FROM (
            SELECT
                v.rendering_npi AS npi,
                v.px AS code,
                LEFT(MAX(pr.procd_name), 80) AS name,
                COUNT(*) AS visit_count,
                ROW_NUMBER() OVER (
                    PARTITION BY v.rendering_npi
                    ORDER BY COUNT(*) DESC, v.px
                ) AS rk
            FROM {mart}.pd_stg_visit v
            INNER JOIN {mart}.pd_provider p ON p.npi = v.rendering_npi
            INNER JOIN {lookup}.procd pr
                ON pr.procd_code = v.px COLLATE {MART_COLLATION}
            WHERE v.px IS NOT NULL AND v.px <> ''
              AND CHAR_LENGTH(TRIM(v.px)) = 5
              AND MOD(v.rendering_npi, {PROVIDER_BUCKETS}) = %s
              AND {primary_procedure_filter_sql("v.px", "pr")}
            GROUP BY v.rendering_npi, v.px
        ) ranked
        WHERE ranked.rk <= {TOP_N}
    """
    return {
        "pd_stg_top_dx": top_dx_sql,
        "pd_stg_top_px": top_px_sql,
        "pd_stg_top_primary_px": top_primary_px_sql,
    }


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
            {top_code_null_sql()}
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


def _window_claim_insert_sql(mart: str, claims: str) -> str:
    return f"""
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


def load_window_claims(
    cur,
    conn,
    mart: str,
    claims: str,
    periods: list[int],
    *,
    log_prefix: str = "phase2",
) -> int:
    window_sql = _window_claim_insert_sql(mart, claims)
    total = 0
    for period in periods:
        for bucket in range(CLAIM_ID_BUCKETS):
            n = _run(cur, conn, window_sql, (period, bucket))
            total += n
            print(f"{log_prefix} window {period} bucket {bucket}: {n} rows", flush=True)
    return total


def delete_window_claims_for_period(
    cur,
    conn,
    mart: str,
    period: int,
    *,
    log_prefix: str = "phase6",
) -> int:
    sql = f"""
        DELETE FROM {mart}.pd_stg_window_claim
        WHERE period_code = %s
          AND MOD(id, {CLAIM_DELETE_BUCKETS}) = %s
    """
    total = 0
    for bucket in range(CLAIM_DELETE_BUCKETS):
        n = _run(cur, conn, sql, (period, bucket))
        total += n
        print(f"{log_prefix} drop {period} bucket {bucket}: {n} rows", flush=True)
    return total


def _rebuild_activity_from_staging(
    cur,
    conn,
    mart: str,
    lookup: str,
    dummy: str,
    periods: list[int],
    counts: dict[str, int],
) -> None:
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
          AND MOD(ABS(t.encounter_id), {VISIT_BUCKETS}) = %s
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

    statements = top_code_statements(mart, lookup)
    for bucket in range(PROVIDER_BUCKETS):
        counts["top_dx_rows"] += _run(cur, conn, statements["pd_stg_top_dx"], (bucket,))
        counts["top_px_rows"] += _run(cur, conn, statements["pd_stg_top_px"], (bucket,))
        counts["top_primary_px_rows"] += _run(cur, conn, statements["pd_stg_top_primary_px"], (bucket,))
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

    top_joins, top_sets = top_code_overlay_sql(mart)
    overlay_sql = f"""
        UPDATE {mart}.pd_provider p
        LEFT JOIN tmp_visit_counts v ON v.npi = p.npi
        LEFT JOIN tmp_panel_counts pan ON pan.npi = p.npi
        {top_joins}
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
            {top_sets},
            p.refreshed_at = NOW()
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """
    for bucket in range(PROVIDER_BUCKETS):
        n = _run(cur, conn, overlay_sql, (bucket,))
        counts["providers_updated"] += n
        print(f"phase2 overlay bucket {bucket}: {n} rows", flush=True)


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
        "top_primary_px_rows": 0,
        "providers_updated": 0,
    }

    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()
        for bucket in range(PROVIDER_BUCKETS):
            _run(cur, conn, reset_activity_columns_sql(mart_db, bucket=bucket))
        counts["window_claims"] = load_window_claims(cur, conn, mart, claims, periods)
        _rebuild_activity_from_staging(cur, conn, mart, lookup, dummy, periods, counts)

    counts["group_summary"] = rebuild_group_summary(conn, mart_db)
    return {
        "window_start": window_start,
        "window_end": window_end,
        "periods": periods,
        **counts,
    }


def slide_activity(
    conn,
    *,
    drop_periods: list[int],
    add_periods: list[int],
    window_start: int,
    window_end: int,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    lookup_db: str = LOOKUP_DB,
) -> dict:
    """Replace months in pd_stg_window_claim and rebuild visits/panel/top from staging.

    Does not DROP pd_stg_window_claim and does not rescan months that stay in the window.
    """
    from provider_directory.locations import Phase2Required, table_has_rows

    if not table_has_rows(conn, mart_db, "pd_stg_window_claim"):
        raise Phase2Required("pd_stg_window_claim is empty. Run phase2 before phase6 --slide.")

    mart = quote_ident(mart_db)
    claims = quote_ident(claims_db)
    lookup = quote_ident(lookup_db)
    dummy = ", ".join(str(n) for n in sorted(DUMMY_NPIS))
    periods = iter_period_codes(window_start, window_end)
    counts: dict[str, int] = {
        "claims_deleted": 0,
        "window_claims": 0,
        "visits": 0,
        "panel_patients": 0,
        "top_dx_rows": 0,
        "top_px_rows": 0,
        "top_primary_px_rows": 0,
        "providers_updated": 0,
    }

    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()
        for period in drop_periods:
            counts["claims_deleted"] += delete_window_claims_for_period(cur, conn, mart, period)
        counts["window_claims"] = load_window_claims(
            cur, conn, mart, claims, add_periods, log_prefix="phase6"
        )

    drop_phase2_derived(conn, mart_db)
    create_schema(conn, mart_db)

    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()
        for bucket in range(PROVIDER_BUCKETS):
            _run(cur, conn, reset_activity_columns_sql(mart_db, bucket=bucket))
        _rebuild_activity_from_staging(cur, conn, mart, lookup, dummy, periods, counts)

    counts["group_summary"] = rebuild_group_summary(conn, mart_db)
    return {
        "window_start": window_start,
        "window_end": window_end,
        "drop_periods": drop_periods,
        "add_periods": add_periods,
        "periods": periods,
        **counts,
    }


def rebuild_top_codes(
    conn,
    *,
    mart_db: str = MART_DB,
    lookup_db: str = LOOKUP_DB,
) -> dict:
    """phase2 --top-codes-only: re-rank diagnoses, procedures and primary procedures
    from the existing pd_stg_visit and overlay only those columns. Leaves
    pd_stg_window_claim, pd_stg_visit, the panel and every later phase's staging
    untouched. Minutes, no claims read."""
    from provider_directory.locations import Phase2Required, table_has_rows

    if not table_has_rows(conn, mart_db, "pd_stg_visit"):
        raise Phase2Required("pd_stg_visit is empty. Run a full phase2 first.")
    create_schema(conn, mart_db)
    mart = quote_ident(mart_db)
    lookup = quote_ident(lookup_db)
    counts: dict[str, int] = {"top_dx_rows": 0, "top_px_rows": 0, "top_primary_px_rows": 0, "providers_updated": 0}
    statements = top_code_statements(mart, lookup)
    top_joins, top_sets = top_code_overlay_sql(mart)
    overlay_sql = f"""
        UPDATE {mart}.pd_provider p
        {top_joins}
        SET
            {top_sets},
            p.refreshed_at = NOW()
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """
    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()
        for table in statements:
            _run(cur, conn, f"TRUNCATE TABLE {mart}.{table}")
        for bucket in range(PROVIDER_BUCKETS):
            counts["top_dx_rows"] += _run(cur, conn, statements["pd_stg_top_dx"], (bucket,))
            counts["top_px_rows"] += _run(cur, conn, statements["pd_stg_top_px"], (bucket,))
            counts["top_primary_px_rows"] += _run(cur, conn, statements["pd_stg_top_primary_px"], (bucket,))
            print(f"phase2 top codes bucket {bucket}", flush=True)
        for bucket in range(PROVIDER_BUCKETS):
            n = _run(cur, conn, overlay_sql, (bucket,))
            counts["providers_updated"] += n
            print(f"phase2 top codes overlay bucket {bucket}: {n} rows", flush=True)
    counts["group_summary"] = rebuild_group_summary(conn, mart_db)
    return {"top_codes_only": True, **counts}
