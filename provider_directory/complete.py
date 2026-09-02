"""Phase 5: referrals, day-of-week mix, prior-year wRVU, specialty benchmarks.

Reads az / azal / Phase 2–4 staging. Writes az_pd only.
Does not rescan Phase 2/3/4 tables and does not drop them.

Referrals reuse az.dash_physician_referrals_to_rendering in the frozen
window. Distinct-patient counts are the warehouse monthly patient_count
summed across months (same limitation as payer mix). Incoming = this NPI
is rendering; outgoing = this NPI is the implied referring physician.

Day-of-week uses az.pat_dt.service_end_date (YYYYMMDD), not period_code.
Visit dates are cached in pd_stg_visit_date and reused on reruns.

Prior-year wRVU is the same visit-grain formula as Phase 4, for
PRIOR_WINDOW_START–PRIOR_WINDOW_END. Cached in pd_stg_npi_wrvu_prior.
"""

from __future__ import annotations

from provider_directory.activity import PROVIDER_BUCKETS, VISIT_BUCKETS, iter_period_codes
from provider_directory.analytics import is_hcpcs_sql, period_in_sql, work_rvu_sql
from provider_directory.db import quote_ident
from provider_directory.locations import table_has_rows
from provider_directory.schema import create_schema, drop_phase5_cached, drop_phase5_staging
from provider_directory.settings import (
    CLAIMS_DB,
    DUMMY_NPIS,
    LOOKUP_DB,
    MART_COLLATION,
    MART_DB,
    MAX_REFERRAL_PEERS,
    PRIOR_WINDOW_END,
    PRIOR_WINDOW_START,
    REFERRAL_IN,
    REFERRAL_OUT,
    WINDOW_END,
    WINDOW_START,
)
from provider_directory.transforms import DOW_PERCENT_COLUMNS, MAX_YOY_CHANGE_PCT, MIN_YOY_PRIOR_WRVU


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


def is_ymd_sql(expr: str) -> str:
    return f"TRIM({expr}) REGEXP '^[0-9]{{8}}'"


def service_date_sql(expr: str) -> str:
    """YYYYMMDD varchar → DATE. No `%` — pymysql would treat it as a placeholder."""
    compact = f"LEFT(TRIM({expr}), 8)"
    return (
        f"CAST(CONCAT(SUBSTRING({compact}, 1, 4), '-', "
        f"SUBSTRING({compact}, 5, 2), '-', SUBSTRING({compact}, 7, 2)) AS DATE)"
    )


def dow_sum_select(alias: str = "d") -> str:
    parts = [
        f"SUM(CASE WHEN {alias}.dow = {int(day)} THEN {alias}.visits ELSE 0 END) AS d{int(day)}"
        for day, _col in DOW_PERCENT_COLUMNS
    ]
    parts.append(f"SUM({alias}.visits) AS tot")
    return ",\n                    ".join(parts)


def dow_overlay_set(src: str = "x", dest: str = "p") -> str:
    return ",\n                ".join(
        f"{dest}.{col} = ROUND(100.0 * {src}.d{int(day)} / NULLIF({src}.tot, 0), 2)"
        for day, col in DOW_PERCENT_COLUMNS
    )


def yoy_change_sql(current_expr: str, prior_expr: str) -> str:
    """Null tiny priors; clamp so DECIMAL cannot overflow (1264)."""
    return f"""
        CASE
            WHEN {current_expr} IS NULL THEN NULL
            WHEN {prior_expr} IS NULL OR {prior_expr} < {MIN_YOY_PRIOR_WRVU} THEN NULL
            ELSE ROUND(LEAST({MAX_YOY_CHANGE_PCT}, GREATEST(-{MAX_YOY_CHANGE_PCT},
                100.0 * ({current_expr} - {prior_expr}) / {prior_expr}
            )), 2)
        END
    """


def provider_phase5_null_sql(mart: str, bucket: int) -> str:
    cols = ",\n                    ".join(f"{name} = NULL" for name in _phase5_provider_names())
    return f"""
        UPDATE {mart}.pd_provider
        SET
                    {cols}
        WHERE MOD(npi, {PROVIDER_BUCKETS}) = {int(bucket)}
    """


def _phase5_provider_names() -> tuple[str, ...]:
    from provider_directory.schema import PD_PROVIDER_PHASE5_COLUMNS

    return tuple(name for name, _def in PD_PROVIDER_PHASE5_COLUMNS)


def rebuild_complete(
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    lookup_db: str = LOOKUP_DB,
    window_start: int = WINDOW_START,
    window_end: int = WINDOW_END,
    prior_start: int = PRIOR_WINDOW_START,
    prior_end: int = PRIOR_WINDOW_END,
    max_peers: int = MAX_REFERRAL_PEERS,
    reuse_cached: bool = True,
) -> dict:
    create_schema(conn, mart_db)
    drop_phase5_staging(conn, mart_db)
    if not reuse_cached:
        drop_phase5_cached(conn, mart_db)
    create_schema(conn, mart_db)

    mart = quote_ident(mart_db)
    claims = quote_ident(claims_db)
    lookup = quote_ident(lookup_db)
    dummy = ", ".join(str(n) for n in sorted(DUMMY_NPIS))
    period_in = period_in_sql(window_start, window_end)
    has_visit = table_has_rows(conn, mart_db, "pd_stg_visit")
    has_visit_site = table_has_rows(conn, mart_db, "pd_stg_visit_site")
    has_npi_sl = table_has_rows(conn, mart_db, "pd_stg_npi_sl")
    has_practice = table_has_rows(conn, mart_db, "pd_provider_practice")
    counts: dict[str, int | str] = {
        "referral_edges": 0,
        "referral_rows": 0,
        "visit_dates": 0,
        "npi_dow_rows": 0,
        "site_dow_rows": 0,
        "providers_dow": 0,
        "practices_dow": 0,
        "prior_wrvu_npi": 0,
        "providers_prior": 0,
        "specialty_rows": 0,
        "providers_benchmark": 0,
    }

    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()
        for bucket in range(PROVIDER_BUCKETS):
            _run(cur, conn, provider_phase5_null_sql(mart, bucket))
        if has_practice:
            dow_null = ", ".join(f"{col} = NULL" for _day, col in DOW_PERCENT_COLUMNS)
            for bucket in range(PROVIDER_BUCKETS):
                _run(
                    cur,
                    conn,
                    f"""
                    UPDATE {mart}.pd_provider_practice
                    SET {dow_null}
                    WHERE MOD(npi, {PROVIDER_BUCKETS}) = {int(bucket)}
                    """,
                )
        _run(cur, conn, f"TRUNCATE TABLE {mart}.pd_provider_referral")

        counts["referral_edges"] = _fill_referral_edges(
            cur, conn, mart, claims, dummy, period_in
        )
        counts["referral_rows"] = _rank_referrals(cur, conn, mart, max_peers)

        if has_visit:
            counts["visit_dates"] = _fill_visit_dates(
                cur, conn, mart, mart_db, claims, window_start, window_end,
                reuse_cached=reuse_cached,
            )
            counts["npi_dow_rows"] = _fill_npi_dow(cur, conn, mart)
            counts["providers_dow"] = _overlay_provider_dow(cur, conn, mart)
            if has_visit_site:
                counts["site_dow_rows"] = _fill_site_dow(cur, conn, mart)
            if has_practice and has_npi_sl:
                counts["practices_dow"] = _overlay_practice_dow(cur, conn, mart)
        else:
            print("phase5 skip DOW: pd_stg_visit is empty", flush=True)

        prior_npi, providers_prior = _fill_prior_wrvu(
            cur,
            conn,
            mart,
            mart_db,
            claims,
            lookup,
            prior_start,
            prior_end,
            reuse_cached=reuse_cached,
        )
        counts["prior_wrvu_npi"] = prior_npi
        counts["providers_prior"] = providers_prior
        counts["specialty_rows"] = _fill_specialty_stats(cur, conn, mart)
        counts["providers_benchmark"] = _overlay_benchmarks(cur, conn, mart)

    return {
        "window_start": window_start,
        "window_end": window_end,
        "prior_window_start": prior_start,
        "prior_window_end": prior_end,
        **counts,
    }


def _fill_referral_edges(cur, conn, mart: str, claims: str, dummy: str, period_in: str) -> int:
    total = 0
    in_sql = f"""
        INSERT INTO {mart}.pd_stg_referral_edge (
            npi, direction, peer_npi, patient_count, claim_count
        )
        SELECT
            d.rendering_physician_code,
            '{REFERRAL_IN}',
            d.implied_all_referring_physician_code,
            SUM(d.patient_count),
            SUM(d.claim_count)
        FROM {claims}.dash_physician_referrals_to_rendering d
        INNER JOIN {mart}.pd_provider rend ON rend.npi = d.rendering_physician_code
        INNER JOIN {mart}.pd_provider refr
            ON refr.npi = d.implied_all_referring_physician_code
        WHERE d.period_code {period_in}
          AND d.rendering_physician_code NOT IN ({dummy})
          AND d.implied_all_referring_physician_code NOT IN ({dummy})
          AND d.rendering_physician_code <> d.implied_all_referring_physician_code
          AND d.rendering_npi_type = 1
          AND d.implied_all_referring_npi_type = 1
          AND MOD(d.rendering_physician_code, {PROVIDER_BUCKETS}) = %s
        GROUP BY d.rendering_physician_code, d.implied_all_referring_physician_code
        ON DUPLICATE KEY UPDATE
            patient_count = {mart}.pd_stg_referral_edge.patient_count + VALUES(patient_count),
            claim_count = {mart}.pd_stg_referral_edge.claim_count + VALUES(claim_count)
    """
    out_sql = f"""
        INSERT INTO {mart}.pd_stg_referral_edge (
            npi, direction, peer_npi, patient_count, claim_count
        )
        SELECT
            d.implied_all_referring_physician_code,
            '{REFERRAL_OUT}',
            d.rendering_physician_code,
            SUM(d.patient_count),
            SUM(d.claim_count)
        FROM {claims}.dash_physician_referrals_to_rendering d
        INNER JOIN {mart}.pd_provider rend ON rend.npi = d.rendering_physician_code
        INNER JOIN {mart}.pd_provider refr
            ON refr.npi = d.implied_all_referring_physician_code
        WHERE d.period_code {period_in}
          AND d.rendering_physician_code NOT IN ({dummy})
          AND d.implied_all_referring_physician_code NOT IN ({dummy})
          AND d.rendering_physician_code <> d.implied_all_referring_physician_code
          AND d.rendering_npi_type = 1
          AND d.implied_all_referring_npi_type = 1
          AND MOD(d.implied_all_referring_physician_code, {PROVIDER_BUCKETS}) = %s
        GROUP BY d.implied_all_referring_physician_code, d.rendering_physician_code
        ON DUPLICATE KEY UPDATE
            patient_count = {mart}.pd_stg_referral_edge.patient_count + VALUES(patient_count),
            claim_count = {mart}.pd_stg_referral_edge.claim_count + VALUES(claim_count)
    """
    for bucket in range(PROVIDER_BUCKETS):
        n_in = _run(cur, conn, in_sql, (bucket,))
        n_out = _run(cur, conn, out_sql, (bucket,))
        total += n_in + n_out
        print(f"phase5 referral edges bucket {bucket}: in {n_in} out {n_out}", flush=True)
    return total


def _rank_referrals(cur, conn, mart: str, max_peers: int) -> int:
    total = 0
    sql = f"""
        INSERT INTO {mart}.pd_provider_referral (
            npi, direction, peer_rank, peer_npi, peer_name, peer_specialty,
            patient_count, claim_count, refreshed_at
        )
        SELECT
            ranked.npi,
            ranked.direction,
            ranked.rk,
            ranked.peer_npi,
            TRIM(BOTH ', ' FROM CONCAT_WS(
                ', ',
                NULLIF(TRIM(peer.last_name), ''),
                NULLIF(TRIM(peer.first_name), '')
            )),
            COALESCE(
                NULLIF(TRIM(peer.primary_specialty_description), ''),
                NULLIF(TRIM(peer.specialty_classification), '')
            ),
            ranked.patient_count,
            ranked.claim_count,
            NOW()
        FROM (
            SELECT
                e.npi,
                e.direction,
                e.peer_npi,
                e.patient_count,
                e.claim_count,
                ROW_NUMBER() OVER (
                    PARTITION BY e.npi, e.direction
                    ORDER BY e.patient_count DESC, e.claim_count DESC, e.peer_npi
                ) AS rk
            FROM {mart}.pd_stg_referral_edge e
            WHERE MOD(e.npi, {PROVIDER_BUCKETS}) = %s
        ) ranked
        INNER JOIN {mart}.pd_provider peer ON peer.npi = ranked.peer_npi
        WHERE ranked.rk <= {int(max_peers)}
    """
    for bucket in range(PROVIDER_BUCKETS):
        n = _run(cur, conn, sql, (bucket,))
        total += n
        print(f"phase5 referral rank bucket {bucket}: {n} rows", flush=True)
    return total


def _fill_visit_dates(
    cur,
    conn,
    mart: str,
    mart_db: str,
    claims: str,
    window_start: int,
    window_end: int,
    *,
    reuse_cached: bool = True,
) -> int:
    if reuse_cached and table_has_rows(conn, mart_db, "pd_stg_visit_date"):
        print("phase5 visit_date reused", flush=True)
        cur.execute(f"SELECT COUNT(*) AS n FROM {mart}.pd_stg_visit_date")
        return int(cur.fetchone()["n"])

    total = 0
    date_expr = service_date_sql("t.service_end_date")
    sql = f"""
        INSERT INTO {mart}.pd_stg_visit_date (encounter_id, service_end_date)
        SELECT
            v.encounter_id,
            MIN({date_expr})
        FROM {claims}.pat_dt t
        INNER JOIN {mart}.pd_stg_visit v ON v.encounter_id = t.encounter_id
        WHERE t.period_code = %s
          AND MOD(IFNULL(t.encounter_id, 0), {VISIT_BUCKETS}) = %s
          AND {is_ymd_sql("t.service_end_date")}
          AND {date_expr} IS NOT NULL
        GROUP BY v.encounter_id
        ON DUPLICATE KEY UPDATE
            service_end_date = LEAST(
                {mart}.pd_stg_visit_date.service_end_date,
                VALUES(service_end_date)
            )
    """
    for period in iter_period_codes(window_start, window_end):
        for bucket in range(VISIT_BUCKETS):
            n = _run(cur, conn, sql, (period, bucket))
            total += n
            print(f"phase5 visit_date {period} bucket {bucket}: {n} rows", flush=True)
    return total


def _fill_npi_dow(cur, conn, mart: str) -> int:
    total = 0
    sql = f"""
        INSERT INTO {mart}.pd_stg_npi_dow (npi, dow, visits)
        SELECT
            v.rendering_npi,
            DAYOFWEEK(d.service_end_date),
            COUNT(*)
        FROM {mart}.pd_stg_visit_date d
        INNER JOIN {mart}.pd_stg_visit v ON v.encounter_id = d.encounter_id
        WHERE MOD(d.encounter_id, {VISIT_BUCKETS}) = %s
          AND v.rendering_npi IS NOT NULL
          AND d.service_end_date IS NOT NULL
        GROUP BY v.rendering_npi, DAYOFWEEK(d.service_end_date)
        ON DUPLICATE KEY UPDATE
            visits = {mart}.pd_stg_npi_dow.visits + VALUES(visits)
    """
    for bucket in range(VISIT_BUCKETS):
        n = _run(cur, conn, sql, (bucket,))
        total += n
        print(f"phase5 npi dow bucket {bucket}: {n} rows", flush=True)
    return total


def _fill_site_dow(cur, conn, mart: str) -> int:
    total = 0
    sql = f"""
        INSERT INTO {mart}.pd_stg_site_dow (npi, sl_code, dow, visits)
        SELECT
            vs.rendering_npi,
            vs.sl_code,
            DAYOFWEEK(d.service_end_date),
            COUNT(*)
        FROM {mart}.pd_stg_visit_date d
        INNER JOIN {mart}.pd_stg_visit_site vs ON vs.encounter_id = d.encounter_id
        WHERE MOD(d.encounter_id, {VISIT_BUCKETS}) = %s
          AND d.service_end_date IS NOT NULL
        GROUP BY vs.rendering_npi, vs.sl_code, DAYOFWEEK(d.service_end_date)
        ON DUPLICATE KEY UPDATE
            visits = {mart}.pd_stg_site_dow.visits + VALUES(visits)
    """
    for bucket in range(VISIT_BUCKETS):
        n = _run(cur, conn, sql, (bucket,))
        total += n
        print(f"phase5 site dow bucket {bucket}: {n} rows", flush=True)
    return total


def _overlay_provider_dow(cur, conn, mart: str) -> int:
    total = 0
    sql = f"""
        UPDATE {mart}.pd_provider p
        INNER JOIN (
            SELECT
                d.npi,
                {dow_sum_select("d")}
            FROM {mart}.pd_stg_npi_dow d
            WHERE MOD(d.npi, {PROVIDER_BUCKETS}) = %s
            GROUP BY d.npi
        ) x ON x.npi = p.npi
        SET
                {dow_overlay_set("x", "p")},
            p.refreshed_at = NOW()
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """
    for bucket in range(PROVIDER_BUCKETS):
        n = _run(cur, conn, sql, (bucket, bucket))
        total += n
        print(f"phase5 provider dow overlay bucket {bucket}: {n} rows", flush=True)
    return total


def _overlay_practice_dow(cur, conn, mart: str) -> int:
    total = 0
    sql = f"""
        UPDATE {mart}.pd_provider_practice pr
        INNER JOIN (
            SELECT
                sl.npi,
                sl.cluster_key,
                {dow_sum_select("d")}
            FROM {mart}.pd_stg_site_dow d
            INNER JOIN {mart}.pd_stg_npi_sl sl
                ON sl.npi = d.npi AND sl.sl_code = d.sl_code
            WHERE MOD(d.npi, {PROVIDER_BUCKETS}) = %s
            GROUP BY sl.npi, sl.cluster_key
        ) x ON x.npi = pr.npi AND x.cluster_key = pr.cluster_key
        SET
                {dow_overlay_set("x", "pr")}
        WHERE MOD(pr.npi, {PROVIDER_BUCKETS}) = %s
    """
    for bucket in range(PROVIDER_BUCKETS):
        n = _run(cur, conn, sql, (bucket, bucket))
        total += n
        print(f"phase5 practice dow overlay bucket {bucket}: {n} rows", flush=True)
    return total


def _fill_prior_wrvu(
    cur,
    conn,
    mart: str,
    mart_db: str,
    claims: str,
    lookup: str,
    prior_start: int,
    prior_end: int,
    *,
    reuse_cached: bool = True,
) -> tuple[int, int]:
    if reuse_cached and table_has_rows(conn, mart_db, "pd_stg_npi_wrvu_prior"):
        print("phase5 prior wrvu reused", flush=True)
        cur.execute(f"SELECT COUNT(*) AS n FROM {mart}.pd_stg_npi_wrvu_prior")
        npi_n = int(cur.fetchone()["n"])
        overlay = _overlay_prior_wrvu(cur, conn, mart)
        return npi_n, overlay

    wrvu_expr = work_rvu_sql("pr")
    sql = f"""
        INSERT INTO {mart}.pd_stg_npi_wrvu_prior (npi, total_wrvu, procedure_count)
        SELECT
            v.rendering_npi,
            SUM({wrvu_expr}),
            COUNT(*)
        FROM (
            SELECT
                t.encounter_id,
                MAX(t.encounter_rendering_physician_code) AS rendering_npi,
                MAX(NULLIF(TRIM(t.encounter_work_procd_code), '')) AS px
            FROM {claims}.pat_dt t
            WHERE t.period_code = %s
              AND MOD(IFNULL(t.encounter_id, 0), {VISIT_BUCKETS}) = %s
              AND t.encounter_id IS NOT NULL AND t.encounter_id <> 0
            GROUP BY t.encounter_id
        ) v
        INNER JOIN {mart}.pd_provider p ON p.npi = v.rendering_npi
        INNER JOIN {lookup}.procd pr
            ON pr.procd_code = TRIM(v.px) COLLATE {MART_COLLATION}
        WHERE v.px IS NOT NULL AND v.px <> ''
          AND {is_hcpcs_sql("v.px")}
          AND {wrvu_expr} IS NOT NULL
        GROUP BY v.rendering_npi
        ON DUPLICATE KEY UPDATE
            total_wrvu = {mart}.pd_stg_npi_wrvu_prior.total_wrvu + VALUES(total_wrvu),
            procedure_count = {mart}.pd_stg_npi_wrvu_prior.procedure_count + VALUES(procedure_count)
    """
    total = 0
    for period in iter_period_codes(prior_start, prior_end):
        for bucket in range(VISIT_BUCKETS):
            n = _run(cur, conn, sql, (period, bucket))
            total += n
            print(f"phase5 prior wrvu {period} bucket {bucket}: {n} rows", flush=True)
    overlay = _overlay_prior_wrvu(cur, conn, mart)
    return total, overlay


def _overlay_prior_wrvu(cur, conn, mart: str) -> int:
    total = 0
    sql = f"""
        UPDATE {mart}.pd_provider p
        INNER JOIN {mart}.pd_stg_npi_wrvu_prior w ON w.npi = p.npi
        SET
            p.wrvu_prior_year_total = ROUND(w.total_wrvu, 2),
            p.wrvu_prior_year_procedure_count = w.procedure_count,
            p.wrvu_prior_year_average = ROUND(w.total_wrvu / NULLIF(w.procedure_count, 0), 3),
            p.wrvu_yoy_change_pct = {yoy_change_sql("p.wrvu_total", "ROUND(w.total_wrvu, 2)")},
            p.refreshed_at = NOW()
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """
    for bucket in range(PROVIDER_BUCKETS):
        n = _run(cur, conn, sql, (bucket,))
        total += n
        print(f"phase5 prior wrvu overlay bucket {bucket}: {n} rows", flush=True)
    return total


def _fill_specialty_stats(cur, conn, mart: str) -> int:
    sql = f"""
        INSERT INTO {mart}.pd_stg_specialty_wrvu (
            specialty_code, npi_count, avg_wrvu, p25_wrvu, median_wrvu, p75_wrvu
        )
        SELECT
            primary_specialty_code,
            cnt,
            avg_wrvu,
            MAX(CASE WHEN rn = p25_rn THEN wrvu_total END),
            MAX(CASE WHEN rn = p50_rn THEN wrvu_total END),
            MAX(CASE WHEN rn = p75_rn THEN wrvu_total END)
        FROM (
            SELECT
                primary_specialty_code,
                wrvu_total,
                ROW_NUMBER() OVER (
                    PARTITION BY primary_specialty_code
                    ORDER BY wrvu_total, npi
                ) AS rn,
                COUNT(*) OVER (PARTITION BY primary_specialty_code) AS cnt,
                ROUND(AVG(wrvu_total) OVER (PARTITION BY primary_specialty_code), 2) AS avg_wrvu,
                GREATEST(1, FLOOR(COUNT(*) OVER (PARTITION BY primary_specialty_code) * 0.25)) AS p25_rn,
                GREATEST(1, FLOOR(COUNT(*) OVER (PARTITION BY primary_specialty_code) * 0.50)) AS p50_rn,
                GREATEST(1, FLOOR(COUNT(*) OVER (PARTITION BY primary_specialty_code) * 0.75)) AS p75_rn
            FROM {mart}.pd_provider
            WHERE wrvu_total IS NOT NULL AND wrvu_total > 0
              AND NULLIF(TRIM(primary_specialty_code), '') IS NOT NULL
        ) ranked
        GROUP BY primary_specialty_code, cnt, avg_wrvu
    """
    n = _run(cur, conn, sql)
    print(f"phase5 specialty stats: {n} rows", flush=True)
    return n


def _overlay_benchmarks(cur, conn, mart: str) -> int:
    _run(
        cur,
        conn,
        f"""
        INSERT INTO {mart}.pd_stg_npi_percentile (npi, pct)
        SELECT
            npi,
            ROUND(100.0 * rn / cnt, 1)
        FROM (
            SELECT
                npi,
                ROW_NUMBER() OVER (
                    PARTITION BY primary_specialty_code
                    ORDER BY wrvu_total, npi
                ) AS rn,
                COUNT(*) OVER (PARTITION BY primary_specialty_code) AS cnt
            FROM {mart}.pd_provider
            WHERE wrvu_total IS NOT NULL AND wrvu_total > 0
              AND NULLIF(TRIM(primary_specialty_code), '') IS NOT NULL
        ) ranked
        """,
    )
    total = 0
    sql = f"""
        UPDATE {mart}.pd_provider p
        INNER JOIN {mart}.pd_stg_specialty_wrvu s
            ON s.specialty_code = p.primary_specialty_code
        LEFT JOIN {mart}.pd_stg_npi_percentile pct ON pct.npi = p.npi
        SET
            p.wrvu_state_specialty_average = s.avg_wrvu,
            p.wrvu_state_specialty_median = s.median_wrvu,
            p.wrvu_state_specialty_p25 = s.p25_wrvu,
            p.wrvu_state_specialty_p75 = s.p75_wrvu,
            p.wrvu_state_specialty_npi_count = s.npi_count,
            p.wrvu_specialty_percentile = pct.pct,
            p.refreshed_at = NOW()
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
          AND p.wrvu_total IS NOT NULL AND p.wrvu_total > 0
    """
    for bucket in range(PROVIDER_BUCKETS):
        n = _run(cur, conn, sql, (bucket,))
        total += n
        print(f"phase5 benchmark overlay bucket {bucket}: {n} rows", flush=True)
    return total
