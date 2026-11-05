"""Phase 5: referrals, day-of-week mix, prior-year work RVU, in-state specialty benchmarks.

Reads az / azal / Phase 2–4 staging. Writes az_pd only.
Does not rescan Phase 2/3/4 tables and does not drop them.

Referrals reuse az.dash_physician_referrals_to_rendering in the frozen
window. Distinct-patient counts are the warehouse monthly patient_count
summed across months (same limitation as payer mix). Incoming = this NPI
is rendering; outgoing = this NPI is the implied referring physician.

Day-of-week uses az.pat_dt.service_end_date (YYYYMMDD), not period_code.
Visit dates are cached in pd_stg_visit_date and reused on reruns.

Prior-year work RVU prices every billed line like Phase 4, for
PRIOR_WINDOW_START–PRIOR_WINDOW_END. The lines are cached in
pd_stg_npi_px_line_prior and re-priced into pd_stg_npi_wrvu_prior each run.
"""

from __future__ import annotations

import re

from provider_directory.activity import PROVIDER_BUCKETS, VISIT_BUCKETS, iter_period_codes
from provider_directory.activity import CLAIM_ID_BUCKETS
from provider_directory.analytics import is_hcpcs_sql, period_in_sql, priced_line_filter_sql, rvu_line_filter_sql
from provider_directory.db import quote_ident
from provider_directory.locations import Phase2Required, table_has_rows
from provider_directory.schema import create_schema, drop_phase5_cached, drop_phase5_staging
from provider_directory.settings import (
    BENCHMARK_MIN_VISITS,
    MARKET_STATE,
    CLAIMS_DB,
    DUMMY_NPIS,
    LOOKUP_DB,
    MART_COLLATION,
    MART_DB,
    MAX_REFERRAL_PEERS,
    PRIOR_WINDOW_END,
    PRIOR_WINDOW_START,
    REFERRAL_EXCLUDE_SAME_ORGANIZATION,
    REFERRAL_EXCLUDED_SPECIALTY_REGEXP,
    REFERRAL_IN,
    REFERRAL_OUT,
    REFERRAL_SOURCE,
    WINDOW_END,
    WINDOW_START,
)
from provider_directory.transforms import DOW_PERCENT_COLUMNS, MAX_YOY_CHANGE_PCT, MIN_YOY_PRIOR_WRVU, strict_work_rvu_sql


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
    market_state: str = MARKET_STATE,
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
        "referral_source": REFERRAL_SOURCE,
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
            cur, conn, mart, claims, dummy, period_in,
            mart_db=mart_db, reuse_cached=reuse_cached,
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
        counts["specialty_rows"] = _fill_specialty_stats(cur, conn, mart, market_state)
        counts["providers_benchmark"] = _overlay_benchmarks(cur, conn, mart, market_state)

    return {
        "window_start": window_start,
        "window_end": window_end,
        "prior_window_start": prior_start,
        "prior_window_end": prior_end,
        **counts,
    }


def _fill_referral_edges(
    cur,
    conn,
    mart: str,
    claims: str,
    dummy: str,
    period_in: str,
    *,
    mart_db: str = MART_DB,
    reuse_cached: bool = True,
    source: str = REFERRAL_SOURCE,
) -> int:
    """Every (npi, direction, peer) edge in the window. settings.REFERRAL_SOURCE
    picks the source: "implied" reads the warehouse referral table, "claims"
    reads the referring NPI recorded on each claim line (pd_stg_window_claim).
    Cached: reused on later runs so peers can be re-ranked without re-reading
    (phase5 --referrals-only); --rebuild-edges refills."""
    if reuse_cached and table_has_rows(conn, mart_db, "pd_stg_referral_edge"):
        print(f"phase5 referral edges reused (source {source})", flush=True)
        return 0
    if source == "claims":
        return _fill_claim_referral_edges(cur, conn, mart, dummy, mart_db=mart_db)
    _run(cur, conn, f"TRUNCATE TABLE {mart}.pd_stg_referral_edge")
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


def claim_referral_edge_sql(mart: str, dummy: str, direction: str) -> str:
    """One provider bucket of claim-named referral edges from pd_stg_window_claim.
    'in': I rendered the line and the peer is its referring_physician_code (who
    sent the patient to me). 'out': the peer rendered the line and I am its
    referring_physician_code (who I sent the patient to). Both NPIs must be Type 1
    clinicians in pd_provider, so organization NPIs written in the referring
    field never become peers. patient_count is distinct patients over the whole
    window and claim_count distinct encounters: the scan is split by pat_id
    bucket, and a patient's lines all fall in one bucket, so the per-bucket
    distinct counts add up exactly. One %s param: the pat_id bucket."""
    if direction == REFERRAL_IN:
        me, peer = "c.rendering_physician_code", "c.referring_physician_code"
    elif direction == REFERRAL_OUT:
        me, peer = "c.referring_physician_code", "c.rendering_physician_code"
    else:
        raise ValueError(f"direction must be {REFERRAL_IN!r} or {REFERRAL_OUT!r}, got {direction!r}")
    return f"""
        INSERT INTO {mart}.pd_stg_referral_edge (
            npi, direction, peer_npi, patient_count, claim_count
        )
        SELECT
            {me},
            '{direction}',
            {peer},
            COUNT(DISTINCT c.pat_id),
            COUNT(DISTINCT c.encounter_id)
        FROM {mart}.pd_stg_window_claim c
        INNER JOIN {mart}.pd_provider rend ON rend.npi = c.rendering_physician_code
        INNER JOIN {mart}.pd_provider refr ON refr.npi = c.referring_physician_code
        WHERE c.referring_physician_code IS NOT NULL
          AND c.rendering_physician_code IS NOT NULL
          AND c.rendering_physician_code NOT IN ({dummy})
          AND c.referring_physician_code NOT IN ({dummy})
          AND c.rendering_physician_code <> c.referring_physician_code
          AND MOD(IFNULL(c.pat_id, 0), {CLAIM_ID_BUCKETS}) = %s
        GROUP BY {me}, {peer}
        ON DUPLICATE KEY UPDATE
            patient_count = {mart}.pd_stg_referral_edge.patient_count + VALUES(patient_count),
            claim_count = {mart}.pd_stg_referral_edge.claim_count + VALUES(claim_count)
    """


def _fill_claim_referral_edges(cur, conn, mart: str, dummy: str, *, mart_db: str = MART_DB) -> int:
    """REFERRAL_SOURCE = "claims": edges from the referring NPI on each window
    claim line. Reads only the mart (Phase 2's pd_stg_window_claim), so it needs
    a Phase 2 that has run for this window."""
    if not table_has_rows(conn, mart_db, "pd_stg_window_claim"):
        raise Phase2Required(
            "pd_stg_window_claim is empty. REFERRAL_SOURCE=claims builds referral edges "
            "from the window claim lines; run phase2 first."
        )
    _run(cur, conn, f"TRUNCATE TABLE {mart}.pd_stg_referral_edge")
    in_sql = claim_referral_edge_sql(mart, dummy, REFERRAL_IN)
    out_sql = claim_referral_edge_sql(mart, dummy, REFERRAL_OUT)
    total = 0
    for bucket in range(CLAIM_ID_BUCKETS):
        n_in = _run(cur, conn, in_sql, (bucket,))
        n_out = _run(cur, conn, out_sql, (bucket,))
        total += n_in + n_out
        print(f"phase5 claim referral edges patient bucket {bucket}: in {n_in} out {n_out}", flush=True)
    return total


def referral_exclusion_sql(me: str = "me", peer: str = "peer") -> str:
    """Predicate that is TRUE for an edge to leave out of the ranked peers:
    same group practice or same claims billing organization (settings.REFERRAL_EXCLUDE_SAME_ORGANIZATION)
    or a peer specialty on settings.REFERRAL_EXCLUDED_SPECIALTY_REGEXP (pathology,
    diagnostic radiology, anesthesiology, lab). Returns '0' when nothing is excluded."""
    parts: list[str] = []
    if REFERRAL_EXCLUDE_SAME_ORGANIZATION:
        parts.append(
            f"({peer}.primary_organization_id IS NOT NULL "
            f"AND {peer}.primary_organization_id = {me}.primary_organization_id)"
        )
        parts.append(
            f"({peer}.billing_organization_id IS NOT NULL "
            f"AND {peer}.billing_organization_id = {me}.billing_organization_id)"
        )
    if REFERRAL_EXCLUDED_SPECIALTY_REGEXP:
        pattern = REFERRAL_EXCLUDED_SPECIALTY_REGEXP.replace("'", "''")
        parts.append(
            f"UPPER(CONCAT_WS(' ', IFNULL({peer}.primary_specialty_description, ''), "
            f"IFNULL({peer}.specialty_classification, ''))) REGEXP '{pattern}'"
        )
    return "(" + " OR ".join(parts) + ")" if parts else "0"


# Taxonomy descriptions that say nothing on their own (physician assistants are
# "Medical" or "Surgical"); the classification column carries the useful word.
GENERIC_SPECIALTY_DESCRIPTIONS = ("MEDICAL", "SURGICAL", "PROFESSIONAL", "UNKNOWN", "OTHER")


def peer_specialty_sql(alias: str = "peer") -> str:
    desc = f"NULLIF(TRIM({alias}.primary_specialty_description), '')"
    cls = f"NULLIF(TRIM({alias}.specialty_classification), '')"
    generic = ", ".join(f"'{g}'" for g in GENERIC_SPECIALTY_DESCRIPTIONS)
    return f"""
        CASE
            WHEN {desc} IS NULL THEN {cls}
            WHEN UPPER({desc}) IN ({generic}) AND {cls} IS NOT NULL
                THEN CONCAT({cls}, ' - ', {desc})
            ELSE {desc}
        END
    """


def _rank_referrals(cur, conn, mart: str, max_peers: int) -> int:
    """Top max_peers per NPI per direction from the edge table, after the
    exclusions, so rank 1..N are the first eligible peers."""
    total = 0
    excluded = referral_exclusion_sql("me", "peer")
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
            {peer_specialty_sql("peer")},
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
            INNER JOIN {mart}.pd_provider me ON me.npi = e.npi
            INNER JOIN {mart}.pd_provider peer ON peer.npi = e.peer_npi
            WHERE MOD(e.npi, {PROVIDER_BUCKETS}) = %s
              AND NOT {excluded}
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
          AND MOD(ABS(IFNULL(t.encounter_id, 0)), {VISIT_BUCKETS}) = %s
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
        WHERE MOD(ABS(d.encounter_id), {VISIT_BUCKETS}) = %s
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
        WHERE MOD(ABS(d.encounter_id), {VISIT_BUCKETS}) = %s
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


def prior_line_scan_sql(mart: str, claims: str) -> str:
    """One prior-window month x pat_id bucket of claim lines into the cached
    pd_stg_npi_px_line_prior (npi, code). Two %s params: period_code, bucket."""
    code = "COALESCE(NULLIF(TRIM(t.work_procd_code), ''), NULLIF(TRIM(t.procd_code), ''))"
    return f"""
        INSERT INTO {mart}.pd_stg_npi_px_line_prior (npi, code, line_count)
        SELECT
            t.rendering_physician_code,
            {code},
            COUNT(*)
        FROM {claims}.pat_dt t
        INNER JOIN {mart}.pd_provider p ON p.npi = t.rendering_physician_code
        WHERE t.period_code = %s
          AND MOD(IFNULL(t.pat_id, 0), {CLAIM_ID_BUCKETS}) = %s
          AND {code} IS NOT NULL
          AND CHAR_LENGTH({code}) = 5
        GROUP BY t.rendering_physician_code, {code}
        ON DUPLICATE KEY UPDATE
            line_count = {mart}.pd_stg_npi_px_line_prior.line_count + VALUES(line_count)
    """


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
    """Prior-year work RVU on the same line-level basis as Phase 4. The line
    staging is cached; pricing into pd_stg_npi_wrvu_prior is redone every run so
    a fee-schedule or rule change is picked up without a rescan."""
    if reuse_cached and table_has_rows(conn, mart_db, "pd_stg_npi_px_line_prior"):
        print("phase5 prior rvu lines reused", flush=True)
    else:
        _run(cur, conn, f"TRUNCATE TABLE {mart}.pd_stg_npi_px_line_prior")
        scan = prior_line_scan_sql(mart, claims)
        for period in iter_period_codes(prior_start, prior_end):
            for bucket in range(CLAIM_ID_BUCKETS):
                n = _run(cur, conn, scan, (period, bucket))
                print(f"phase5 prior rvu lines {period} bucket {bucket}: {n} rows", flush=True)

    wrvu_expr = strict_work_rvu_sql("pr")
    price_sql = f"""
        INSERT INTO {mart}.pd_stg_npi_wrvu_prior (npi, total_wrvu, procedure_count)
        SELECT
            l.npi,
            SUM(l.line_count * {wrvu_expr}),
            SUM(l.line_count)
        FROM {mart}.pd_stg_npi_px_line_prior l
        INNER JOIN {mart}.pd_provider p ON p.npi = l.npi
        INNER JOIN {lookup}.procd pr
            ON pr.procd_code = l.code COLLATE {MART_COLLATION}
        WHERE MOD(l.npi, {PROVIDER_BUCKETS}) = %s
          AND {priced_line_filter_sql("l.code", "pr")}
          AND {rvu_line_filter_sql("p", "pr")}
        GROUP BY l.npi
    """
    total = 0
    for bucket in range(PROVIDER_BUCKETS):
        n = _run(cur, conn, price_sql, (bucket,))
        total += n
        print(f"phase5 prior wrvu bucket {bucket}: {n} rows", flush=True)
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


def benchmark_peer_where(mart: str, state: str, alias: str = "p") -> str:
    """Peer set for the specialty benchmarks and percentiles: a specialty on file,
    at least BENCHMARK_MIN_VISITS visits in the window, and a practice site in the
    market state (pd_provider_practice)."""
    if not re.fullmatch(r"[A-Z]{2}", state or ""):
        raise ValueError(f"state must be a USPS code, got {state!r}")
    return (
        f"NULLIF(TRIM({alias}.primary_specialty_code), '') IS NOT NULL "
        f"AND IFNULL({alias}.visits_total, 0) >= {int(BENCHMARK_MIN_VISITS)} "
        f"AND EXISTS (SELECT 1 FROM {mart}.pd_provider_practice pr "
        f"WHERE pr.npi = {alias}.npi AND pr.state = '{state}')"
    )


def _fill_specialty_stats(cur, conn, mart: str, state: str = MARKET_STATE) -> int:
    peers = benchmark_peer_where(mart, state)
    sql = f"""
        INSERT INTO {mart}.pd_stg_specialty_wrvu (
            specialty_code, npi_count, avg_wrvu, avg_wrvu_per_procedure, p25_wrvu, median_wrvu, p75_wrvu,
            p25_wrvu_per_procedure, median_wrvu_per_procedure, p75_wrvu_per_procedure
        )
        SELECT
            primary_specialty_code,
            cnt,
            avg_wrvu,
            ROUND(SUM(wrvu_total) / NULLIF(SUM(wrvu_procedure_count), 0), 3),
            MAX(CASE WHEN rn = p25_rn THEN wrvu_total END),
            MAX(CASE WHEN rn = p50_rn THEN wrvu_total END),
            MAX(CASE WHEN rn = p75_rn THEN wrvu_total END),
            MAX(CASE WHEN rn_avg = p25_rn THEN wrvu_average END),
            MAX(CASE WHEN rn_avg = p50_rn THEN wrvu_average END),
            MAX(CASE WHEN rn_avg = p75_rn THEN wrvu_average END)
        FROM (
            SELECT
                primary_specialty_code,
                wrvu_total,
                wrvu_procedure_count,
                wrvu_average,
                ROW_NUMBER() OVER (
                    PARTITION BY primary_specialty_code
                    ORDER BY wrvu_total, npi
                ) AS rn,
                ROW_NUMBER() OVER (
                    PARTITION BY primary_specialty_code
                    ORDER BY wrvu_average, npi
                ) AS rn_avg,
                COUNT(*) OVER (PARTITION BY primary_specialty_code) AS cnt,
                ROUND(AVG(wrvu_total) OVER (PARTITION BY primary_specialty_code), 2) AS avg_wrvu,
                GREATEST(1, FLOOR(COUNT(*) OVER (PARTITION BY primary_specialty_code) * 0.25)) AS p25_rn,
                GREATEST(1, FLOOR(COUNT(*) OVER (PARTITION BY primary_specialty_code) * 0.50)) AS p50_rn,
                GREATEST(1, FLOOR(COUNT(*) OVER (PARTITION BY primary_specialty_code) * 0.75)) AS p75_rn
            FROM {mart}.pd_provider p
            WHERE p.wrvu_total IS NOT NULL AND p.wrvu_total > 0
              AND {peers}
        ) ranked
        GROUP BY primary_specialty_code, cnt, avg_wrvu
    """
    n = _run(cur, conn, sql)
    print(f"phase5 specialty stats: {n} rows", flush=True)
    return n


def _overlay_benchmarks(cur, conn, mart: str, state: str = MARKET_STATE) -> int:
    peers = benchmark_peer_where(mart, state)
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
            FROM {mart}.pd_provider p
            WHERE p.wrvu_total IS NOT NULL AND p.wrvu_total > 0
              AND {peers}
        ) ranked
        """,
    )
    _run(
        cur,
        conn,
        f"""
        INSERT INTO {mart}.pd_stg_npi_avg_percentile (npi, pct)
        SELECT
            npi,
            ROUND(100.0 * rn / cnt, 1)
        FROM (
            SELECT
                npi,
                ROW_NUMBER() OVER (
                    PARTITION BY primary_specialty_code
                    ORDER BY wrvu_average, npi
                ) AS rn,
                COUNT(*) OVER (PARTITION BY primary_specialty_code) AS cnt
            FROM {mart}.pd_provider p
            WHERE p.wrvu_average IS NOT NULL AND p.wrvu_average > 0
              AND {peers}
        ) ranked
        """,
    )
    _run(
        cur,
        conn,
        f"""
        INSERT INTO {mart}.pd_stg_npi_visits_percentile (npi, pct)
        SELECT
            npi,
            ROUND(100.0 * rn / cnt, 1)
        FROM (
            SELECT
                npi,
                ROW_NUMBER() OVER (
                    PARTITION BY primary_specialty_code
                    ORDER BY visits_total, npi
                ) AS rn,
                COUNT(*) OVER (PARTITION BY primary_specialty_code) AS cnt
            FROM {mart}.pd_provider p
            WHERE {peers}
        ) ranked
        """,
    )
    total = 0
    sql = f"""
        UPDATE {mart}.pd_provider p
        LEFT JOIN {mart}.pd_stg_specialty_wrvu s
            ON s.specialty_code = p.primary_specialty_code
        LEFT JOIN {mart}.pd_stg_npi_percentile wrvu ON wrvu.npi = p.npi
        LEFT JOIN {mart}.pd_stg_npi_avg_percentile avgp ON avgp.npi = p.npi
        LEFT JOIN {mart}.pd_stg_npi_visits_percentile vis ON vis.npi = p.npi
        SET
            p.wrvu_state_specialty_average = IF(p.wrvu_total IS NOT NULL AND p.wrvu_total > 0, s.avg_wrvu, p.wrvu_state_specialty_average),
            p.wrvu_state_specialty_average_per_procedure = IF(p.wrvu_total IS NOT NULL AND p.wrvu_total > 0, s.avg_wrvu_per_procedure, p.wrvu_state_specialty_average_per_procedure),
            p.wrvu_state_specialty_median_per_procedure = IF(p.wrvu_total IS NOT NULL AND p.wrvu_total > 0, s.median_wrvu_per_procedure, p.wrvu_state_specialty_median_per_procedure),
            p.wrvu_state_specialty_p25_per_procedure = IF(p.wrvu_total IS NOT NULL AND p.wrvu_total > 0, s.p25_wrvu_per_procedure, p.wrvu_state_specialty_p25_per_procedure),
            p.wrvu_state_specialty_p75_per_procedure = IF(p.wrvu_total IS NOT NULL AND p.wrvu_total > 0, s.p75_wrvu_per_procedure, p.wrvu_state_specialty_p75_per_procedure),
            p.wrvu_average_specialty_percentile = avgp.pct,
            p.wrvu_state_specialty_median = IF(p.wrvu_total IS NOT NULL AND p.wrvu_total > 0, s.median_wrvu, p.wrvu_state_specialty_median),
            p.wrvu_state_specialty_p25 = IF(p.wrvu_total IS NOT NULL AND p.wrvu_total > 0, s.p25_wrvu, p.wrvu_state_specialty_p25),
            p.wrvu_state_specialty_p75 = IF(p.wrvu_total IS NOT NULL AND p.wrvu_total > 0, s.p75_wrvu, p.wrvu_state_specialty_p75),
            p.wrvu_state_specialty_npi_count = IF(p.wrvu_total IS NOT NULL AND p.wrvu_total > 0, s.npi_count, p.wrvu_state_specialty_npi_count),
            p.wrvu_specialty_percentile = wrvu.pct,
            p.visits_specialty_percentile = vis.pct,
            p.activity_specialty_percentile = CASE
                WHEN vis.pct IS NOT NULL AND wrvu.pct IS NOT NULL
                    THEN ROUND((vis.pct + wrvu.pct) / 2, 1)
                ELSE COALESCE(vis.pct, wrvu.pct)
            END,
            p.refreshed_at = NOW()
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
          AND (
                (p.wrvu_total IS NOT NULL AND p.wrvu_total > 0)
             OR IFNULL(p.visits_total, 0) > 0
          )
    """
    for bucket in range(PROVIDER_BUCKETS):
        n = _run(cur, conn, sql, (bucket,))
        total += n
        print(f"phase5 benchmark overlay bucket {bucket}: {n} rows", flush=True)
    return total


def rebuild_referrals(
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
    window_start: int = WINDOW_START,
    window_end: int = WINDOW_END,
    max_peers: int = MAX_REFERRAL_PEERS,
    rebuild_edges: bool = False,
) -> dict:
    """phase5 --referrals-only: re-rank referral peers from the cached edge table
    with the current exclusions and MAX_REFERRAL_PEERS. Reads the warehouse only
    when the edge table is empty or rebuild_edges is set. Minutes."""
    create_schema(conn, mart_db)
    mart = quote_ident(mart_db)
    claims = quote_ident(claims_db)
    dummy = ", ".join(str(n) for n in sorted(DUMMY_NPIS))
    period_in = period_in_sql(window_start, window_end)
    counts: dict[str, int | bool | str] = {
        "referrals_only": True,
        "referral_source": REFERRAL_SOURCE,
        "referral_edges": 0,
        "referral_rows": 0,
    }
    with conn.cursor() as cur:
        _session_timeouts(cur)
        conn.commit()
        counts["referral_edges"] = _fill_referral_edges(
            cur, conn, mart, claims, dummy, period_in,
            mart_db=mart_db, reuse_cached=not rebuild_edges,
        )
        _run(cur, conn, f"TRUNCATE TABLE {mart}.pd_provider_referral")
        counts["referral_rows"] = _rank_referrals(cur, conn, mart, max_peers)
    return {"window_start": window_start, "window_end": window_end, **counts}
