"""Cheap competitive extras. No az.pat_dt rescan.

Overlays group size, PDC telehealth Y/N, secondary specialties, new vs
established E/M, POS mix (including telehealth POS 02/10), MIPS, Open
Payments, and Care Compare utilization onto the existing mart.
"""

from __future__ import annotations

import io
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from provider_directory.cms import (
    cached_path,
    clinician_csv_url,
    download_file,
    mips_csv_url,
    open_payments_detail_urls,
    utilization_csv_url,
)
from provider_directory.cms.load import (
    load_pdc_clinician,
    load_pdc_mips,
    load_pdc_utilization,
    load_spine_npis,
    replace_open_payments,
)
from provider_directory.cms.parse import iter_csv_rows, parse_open_payments_row
from provider_directory.db import quote_ident
from provider_directory.locations import table_has_rows
from provider_directory.mart import PROVIDER_BUCKETS, pdc_identity_rank_order_sql
from provider_directory.schema import create_schema
from provider_directory.settings import (
    CLAIMS_DB,
    CMS_CACHE_DIR,
    ESTABLISHED_PX,
    MARKET_STATE,
    MART_DB,
    MAX_UTILIZATION_CATEGORIES,
    NEW_PATIENT_PX,
    POS_ASC,
    POS_ED,
    POS_HOPD,
    POS_OFFICE,
    POS_TELEHEALTH,
)


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


def _in_sql(values: tuple[int, ...] | tuple[str, ...]) -> str:
    return ", ".join(str(int(v)) if isinstance(v, int) else f"'{v}'" for v in values)


def px_in_sql(alias: str, codes: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{code}'" for code in codes)
    return f"TRIM({alias}.px) IN ({quoted})"


def pos_in_sql(alias: str, codes: tuple[int, ...]) -> str:
    return f"{alias}.pos_type_code IN ({_in_sql(codes)})"


def known_pos_sql(alias: str) -> str:
    codes = POS_OFFICE + POS_HOPD + POS_ASC + POS_ED + POS_TELEHEALTH
    return f"{alias}.pos_type_code IN ({_in_sql(codes)})"


def percent_sql(num: str, den: str) -> str:
    return f"ROUND(100 * {num} / NULLIF({den}, 0), 2)"


def _filename_from_url(url: str, fallback: str) -> str:
    name = Path(unquote(urlparse(url).path)).name
    return name or fallback


def find_cached_extras() -> dict[str, Path]:
    found: dict[str, Path] = {}
    mips = cached_path("ec_score_file.csv")
    if mips.exists():
        found["mips"] = mips
    else:
        matches = sorted(CMS_CACHE_DIR.glob("*ec_score_file*.csv")) or sorted(
            CMS_CACHE_DIR.glob("*MIPS*.csv")
        )
        if matches:
            found["mips"] = matches[-1]
    util_matches = sorted(CMS_CACHE_DIR.glob("Utilization*.csv"))
    if util_matches:
        found["utilization"] = util_matches[-1]
    return found


def download_extras_files(
    *,
    skip_mips: bool = False,
    skip_utilization: bool = False,
    skip_open_payments: bool = False,
    download_pdc: bool = False,
    year: int | None = None,
    session: requests.Session | None = None,
) -> dict[str, Path | str | int]:
    http = session or requests.Session()
    CMS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path | str | int] = {}
    if download_pdc:
        clinician_url = clinician_csv_url(session=http)
        paths["clinician"] = download_file(
            clinician_url,
            cached_path("DAC_NationalDownloadableFile.csv"),
            session=http,
        )
    if not skip_mips:
        url = mips_csv_url(session=http)
        paths["mips"] = download_file(
            url, cached_path(_filename_from_url(url, "ec_score_file.csv")), session=http
        )
    if not skip_utilization:
        url = utilization_csv_url(session=http)
        paths["utilization"] = download_file(
            url, cached_path("Utilization.csv"), session=http
        )
    if not skip_open_payments:
        program_year, urls = open_payments_detail_urls(year, session=http)
        paths["open_payments_year"] = program_year
        paths["open_payments_urls"] = urls
    return paths


def iter_http_csv(url: str, session: requests.Session | None = None):
    http = session or requests.Session()
    with http.get(url, stream=True, timeout=(60, 600)) as response:
        response.raise_for_status()
        response.raw.decode_content = True
        handle = io.TextIOWrapper(response.raw, encoding="utf-8-sig", errors="replace", newline="")
        yield from iter_csv_rows(handle)


def accumulate_open_payments(
    rows,
    spine_npis: set[int],
    *,
    kind: str,
    totals: dict[int, dict[str, list[float]]],
) -> int:
    kept = 0
    scanned = 0
    for raw in rows:
        scanned += 1
        if scanned % 1_000_000 == 0:
            print(f"open payments {kind}: scanned {scanned:,}", flush=True)
        parsed = parse_open_payments_row(raw)
        if parsed is None or parsed["npi"] not in spine_npis:
            continue
        slot = totals.setdefault(parsed["npi"], {}).setdefault(kind, [0.0, 0.0])
        slot[0] += parsed["amount"]
        slot[1] += 1
        kept += 1
    print(f"open payments {kind}: scanned {scanned:,}, spine hits {kept:,}", flush=True)
    return kept


def open_payments_insert_rows(
    totals: dict[int, dict[str, list[float]]],
    program_year: int,
) -> list[dict]:
    rows = []
    for npi, kinds in totals.items():
        for kind, (total, count) in kinds.items():
            rows.append(
                {
                    "npi": npi,
                    "program_year": program_year,
                    "payment_kind": kind,
                    "total": round(total, 2),
                    "payment_count": int(count),
                }
            )
    return rows


def overlay_pdc_extras(cur, conn, mart_db: str = MART_DB, market_state: str = MARKET_STATE) -> int:
    mart = quote_ident(mart_db)
    updated = 0
    sql = f"""
        UPDATE {mart}.pd_provider p
        INNER JOIN (
            SELECT *
            FROM (
                SELECT
                    npi,
                    num_org_mem,
                    telehlth,
                    sec_spec_1,
                    sec_spec_2,
                    sec_spec_3,
                    sec_spec_4,
                    ROW_NUMBER() OVER (
                        PARTITION BY npi
                        ORDER BY {pdc_identity_rank_order_sql()}
                    ) AS rn
                FROM {mart}.cms_pdc_clinician
            ) ranked
            WHERE rn = 1
        ) c ON c.npi = p.npi
        SET
            p.group_size = c.num_org_mem,
            p.telehealth_offered = CASE
                WHEN UPPER(TRIM(IFNULL(c.telehlth, ''))) IN ('Y', 'YES', 'TRUE', '1') THEN 1
                WHEN UPPER(TRIM(IFNULL(c.telehlth, ''))) IN ('N', 'NO', 'FALSE', '0') THEN 0
                ELSE NULL
            END,
            p.secondary_specialty_1 = NULLIF(TRIM(c.sec_spec_1), ''),
            p.secondary_specialty_2 = NULLIF(TRIM(c.sec_spec_2), ''),
            p.secondary_specialty_3 = NULLIF(TRIM(c.sec_spec_3), ''),
            p.secondary_specialty_4 = NULLIF(TRIM(c.sec_spec_4), '')
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """
    for bucket in range(PROVIDER_BUCKETS):
        updated += _run(cur, conn, sql, (market_state, bucket))
        print(f"extras pdc overlay bucket {bucket}: {updated} cumulative", flush=True)
    return updated


def overlay_em(cur, conn, mart_db: str = MART_DB) -> int:
    mart = quote_ident(mart_db)
    new_sql = px_in_sql("v", NEW_PATIENT_PX)
    est_sql = px_in_sql("v", ESTABLISHED_PX)
    reset = f"""
        UPDATE {mart}.pd_provider
        SET
            visits_new_patient = NULL,
            visits_established = NULL,
            visits_percent_new_patient = NULL
        WHERE MOD(npi, {PROVIDER_BUCKETS}) = %s
    """
    fill = f"""
        UPDATE {mart}.pd_provider p
        INNER JOIN (
            SELECT
                v.rendering_npi AS npi,
                SUM(CASE WHEN {new_sql} THEN 1 ELSE 0 END) AS visits_new_patient,
                SUM(CASE WHEN {est_sql} THEN 1 ELSE 0 END) AS visits_established
            FROM {mart}.pd_stg_visit v
            WHERE MOD(v.rendering_npi, {PROVIDER_BUCKETS}) = %s
            GROUP BY v.rendering_npi
        ) x ON x.npi = p.npi
        SET
            p.visits_new_patient = x.visits_new_patient,
            p.visits_established = x.visits_established,
            p.visits_percent_new_patient = {percent_sql(
                "x.visits_new_patient",
                "x.visits_new_patient + x.visits_established",
            )}
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """
    updated = 0
    for bucket in range(PROVIDER_BUCKETS):
        _run(cur, conn, reset, (bucket,))
        updated += _run(cur, conn, fill, (bucket, bucket))
        print(f"extras e/m bucket {bucket}", flush=True)
    return updated


def overlay_pos(
    cur,
    conn,
    *,
    mart_db: str = MART_DB,
    claims_db: str = CLAIMS_DB,
) -> int:
    mart = quote_ident(mart_db)
    claims = quote_ident(claims_db)
    office = pos_in_sql("sl", POS_OFFICE)
    hopd = pos_in_sql("sl", POS_HOPD)
    asc = pos_in_sql("sl", POS_ASC)
    ed = pos_in_sql("sl", POS_ED)
    tele = pos_in_sql("sl", POS_TELEHEALTH)
    known = known_pos_sql("sl")
    reset = f"""
        UPDATE {mart}.pd_provider
        SET
            visits_percent_office = NULL,
            visits_percent_hopd = NULL,
            visits_percent_asc = NULL,
            visits_percent_ed = NULL,
            visits_percent_telehealth = NULL,
            visits_percent_other_pos = NULL
        WHERE MOD(npi, {PROVIDER_BUCKETS}) = %s
    """
    fill = f"""
        UPDATE {mart}.pd_provider p
        INNER JOIN (
            SELECT
                vs.rendering_npi AS npi,
                COUNT(*) AS visits,
                SUM(CASE WHEN {office} THEN 1 ELSE 0 END) AS visits_office,
                SUM(CASE WHEN {hopd} THEN 1 ELSE 0 END) AS visits_hopd,
                SUM(CASE WHEN {asc} THEN 1 ELSE 0 END) AS visits_asc,
                SUM(CASE WHEN {ed} THEN 1 ELSE 0 END) AS visits_ed,
                SUM(CASE WHEN {tele} THEN 1 ELSE 0 END) AS visits_telehealth,
                SUM(CASE WHEN {known} THEN 0 ELSE 1 END) AS visits_other_pos
            FROM {mart}.pd_stg_visit_site vs
            INNER JOIN {claims}.sl sl ON sl.sl_code = vs.sl_code
            WHERE MOD(vs.rendering_npi, {PROVIDER_BUCKETS}) = %s
            GROUP BY vs.rendering_npi
        ) x ON x.npi = p.npi
        SET
            p.visits_percent_office = {percent_sql("x.visits_office", "x.visits")},
            p.visits_percent_hopd = {percent_sql("x.visits_hopd", "x.visits")},
            p.visits_percent_asc = {percent_sql("x.visits_asc", "x.visits")},
            p.visits_percent_ed = {percent_sql("x.visits_ed", "x.visits")},
            p.visits_percent_telehealth = {percent_sql("x.visits_telehealth", "x.visits")},
            p.visits_percent_other_pos = {percent_sql("x.visits_other_pos", "x.visits")}
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """
    updated = 0
    for bucket in range(PROVIDER_BUCKETS):
        _run(cur, conn, reset, (bucket,))
        updated += _run(cur, conn, fill, (bucket, bucket))
        print(f"extras pos bucket {bucket}", flush=True)
    return updated


def overlay_mips(cur, conn, mart_db: str = MART_DB) -> int:
    mart = quote_ident(mart_db)
    reset = f"""
        UPDATE {mart}.pd_provider
        SET mips_final_score = NULL, mips_quality_score = NULL
        WHERE MOD(npi, {PROVIDER_BUCKETS}) = %s
    """
    matched = f"""
        UPDATE {mart}.pd_provider p
        INNER JOIN {mart}.pd_npi_xwalk x ON x.npi = p.npi
        INNER JOIN (
            SELECT npi, org_pac_id, MAX(final_score) AS final_score, MAX(quality_score) AS quality_score
            FROM {mart}.cms_pdc_mips
            GROUP BY npi, org_pac_id
        ) m ON m.npi = p.npi AND m.org_pac_id = IFNULL(x.org_pac_id, '')
        SET
            p.mips_final_score = m.final_score,
            p.mips_quality_score = m.quality_score
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """
    fallback = f"""
        UPDATE {mart}.pd_provider p
        INNER JOIN (
            SELECT npi, MAX(final_score) AS final_score, MAX(quality_score) AS quality_score
            FROM {mart}.cms_pdc_mips
            GROUP BY npi
        ) m ON m.npi = p.npi
        SET
            p.mips_final_score = COALESCE(p.mips_final_score, m.final_score),
            p.mips_quality_score = COALESCE(p.mips_quality_score, m.quality_score)
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """
    updated = 0
    for bucket in range(PROVIDER_BUCKETS):
        _run(cur, conn, reset, (bucket,))
        updated += _run(cur, conn, matched, (bucket,))
        updated += _run(cur, conn, fallback, (bucket,))
        print(f"extras mips bucket {bucket}", flush=True)
    return updated


def overlay_open_payments(cur, conn, mart_db: str = MART_DB, program_year: int | None = None) -> int:
    mart = quote_ident(mart_db)
    if program_year is None:
        year_filter = f"program_year = (SELECT MAX(program_year) FROM {mart}.cms_open_payments)"
        year_params: tuple = ()
    else:
        year_filter = "program_year = %s"
        year_params = (int(program_year),)
    reset = f"""
        UPDATE {mart}.pd_provider
        SET
            open_payments_year = NULL,
            open_payments_general_total = NULL,
            open_payments_research_total = NULL,
            open_payments_ownership_total = NULL,
            open_payments_count = NULL
        WHERE MOD(npi, {PROVIDER_BUCKETS}) = %s
    """
    fill = f"""
        UPDATE {mart}.pd_provider p
        INNER JOIN (
            SELECT
                npi,
                MAX(program_year) AS program_year,
                SUM(CASE WHEN payment_kind = 'general' THEN total ELSE 0 END) AS general_total,
                SUM(CASE WHEN payment_kind = 'research' THEN total ELSE 0 END) AS research_total,
                SUM(CASE WHEN payment_kind = 'ownership' THEN total ELSE 0 END) AS ownership_total,
                SUM(payment_count) AS payment_count
            FROM {mart}.cms_open_payments
            WHERE {year_filter}
            GROUP BY npi
        ) o ON o.npi = p.npi
        SET
            p.open_payments_year = o.program_year,
            p.open_payments_general_total = o.general_total,
            p.open_payments_research_total = o.research_total,
            p.open_payments_ownership_total = o.ownership_total,
            p.open_payments_count = o.payment_count
        WHERE MOD(p.npi, {PROVIDER_BUCKETS}) = %s
    """
    updated = 0
    for bucket in range(PROVIDER_BUCKETS):
        _run(cur, conn, reset, (bucket,))
        updated += _run(cur, conn, fill, (*year_params, bucket))
        print(f"extras open payments bucket {bucket}", flush=True)
    return updated


def overlay_utilization(cur, conn, mart_db: str = MART_DB) -> int:
    mart = quote_ident(mart_db)
    _run(cur, conn, f"TRUNCATE TABLE {mart}.pd_provider_utilization")
    sql = f"""
        INSERT INTO {mart}.pd_provider_utilization (
            npi, rk, procedure_category, count_label, percentile, profile_display
        )
        SELECT npi, rk, procedure_category, count_label, percentile, profile_display
        FROM (
            SELECT
                npi,
                procedure_category,
                count_label,
                percentile,
                profile_display,
                ROW_NUMBER() OVER (
                    PARTITION BY npi
                    ORDER BY IFNULL(percentile, 0) DESC, procedure_category
                ) AS rk
            FROM {mart}.cms_pdc_utilization
        ) ranked
        WHERE rk <= {int(MAX_UTILIZATION_CATEGORIES)}
    """
    return _run(cur, conn, sql)


def _load_open_payments_from_urls(
    conn,
    urls: dict[str, str],
    spine_npis: set[int],
    program_year: int,
    *,
    mart_db: str = MART_DB,
    session: requests.Session | None = None,
) -> int:
    totals: dict[int, dict[str, list[float]]] = defaultdict(dict)
    http = session or requests.Session()
    for kind, url in urls.items():
        print(f"open payments streaming {kind} {url}", flush=True)
        accumulate_open_payments(
            iter_http_csv(url, session=http),
            spine_npis,
            kind=kind,
            totals=totals,
        )
    rows = open_payments_insert_rows(totals, program_year)
    return replace_open_payments(conn, rows, mart_db=mart_db, program_year=program_year)


def rebuild_extras(
    conn,
    *,
    mart_db: str = MART_DB,
    download: bool = False,
    reload_pdc: bool = False,
    skip_mips: bool = False,
    skip_utilization: bool = False,
    skip_open_payments: bool = False,
    year: int | None = None,
) -> dict:
    """Overlay extras onto pd_provider. Never truncates the spine. Never scans pat_dt."""
    create_schema(conn, mart_db)
    skipped: list[str] = []
    loaded = {"clinician": 0, "mips": 0, "utilization": 0, "open_payments": 0}
    downloaded: dict[str, str | int] = {}
    overlays: dict[str, int] = {}
    open_payments_year: int | None = year
    open_payments_urls: dict[str, str] | None = None
    if download:
        extras_paths = download_extras_files(
            skip_mips=skip_mips,
            skip_utilization=skip_utilization,
            skip_open_payments=skip_open_payments,
            download_pdc=reload_pdc,
            year=year,
        )
        for key, value in extras_paths.items():
            if isinstance(value, Path):
                downloaded[key] = str(value)
            elif key == "open_payments_year":
                open_payments_year = int(value)
                downloaded[key] = int(value)
            elif key == "open_payments_urls" and isinstance(value, dict):
                open_payments_urls = value

    from provider_directory.pipeline import find_cached_cms

    spine_npis = load_spine_npis(conn, mart_db=mart_db)
    cached_cms = find_cached_cms()
    cached_extras = find_cached_extras()

    if reload_pdc:
        if "clinician" in cached_cms:
            loaded["clinician"] = load_pdc_clinician(
                conn, cached_cms["clinician"], spine_npis, mart_db=mart_db
            )
        else:
            skipped.append("PDC clinician CSV not in data/cms — extras --reload-pdc --download")

    if not skip_mips:
        if "mips" in cached_extras:
            loaded["mips"] = load_pdc_mips(conn, cached_extras["mips"], spine_npis, mart_db=mart_db)
        elif download:
            skipped.append("MIPS CSV did not land in data/cms")
        else:
            skipped.append("MIPS CSV not cached — extras --download")

    if not skip_utilization:
        if "utilization" in cached_extras:
            loaded["utilization"] = load_pdc_utilization(
                conn, cached_extras["utilization"], spine_npis, mart_db=mart_db
            )
        elif download:
            skipped.append("Utilization CSV did not land in data/cms")
        else:
            skipped.append("Utilization CSV not cached — extras --download")

    with conn.cursor() as cur:
        _session_timeouts(cur)
        if table_has_rows(conn, mart_db, "cms_pdc_clinician"):
            overlays["pdc"] = overlay_pdc_extras(cur, conn, mart_db=mart_db)
        else:
            skipped.append("cms_pdc_clinician empty — skip group size / telehealth / sec spec")
        if table_has_rows(conn, mart_db, "pd_stg_visit"):
            overlays["em"] = overlay_em(cur, conn, mart_db=mart_db)
        else:
            skipped.append("pd_stg_visit empty — skip new vs established")
        if table_has_rows(conn, mart_db, "pd_stg_visit_site"):
            overlays["pos"] = overlay_pos(cur, conn, mart_db=mart_db)
        else:
            skipped.append("pd_stg_visit_site empty — skip POS mix")
        if not skip_mips and table_has_rows(conn, mart_db, "cms_pdc_mips"):
            overlays["mips"] = overlay_mips(cur, conn, mart_db=mart_db)
        if not skip_utilization and table_has_rows(conn, mart_db, "cms_pdc_utilization"):
            overlays["utilization"] = overlay_utilization(cur, conn, mart_db=mart_db)
        cur.execute(f"UPDATE {quote_ident(mart_db)}.pd_provider SET refreshed_at = NOW()")
        conn.commit()

    if open_payments_urls:
        loaded["open_payments"] = _load_open_payments_from_urls(
            conn, open_payments_urls, spine_npis, int(open_payments_year), mart_db=mart_db
        )
        with conn.cursor() as cur:
            _session_timeouts(cur)
            overlays["open_payments"] = overlay_open_payments(
                cur, conn, mart_db=mart_db, program_year=open_payments_year
            )
            cur.execute(f"UPDATE {quote_ident(mart_db)}.pd_provider SET refreshed_at = NOW()")
            conn.commit()
    elif not skip_open_payments and table_has_rows(conn, mart_db, "cms_open_payments"):
        with conn.cursor() as cur:
            overlays["open_payments"] = overlay_open_payments(
                cur, conn, mart_db=mart_db, program_year=open_payments_year
            )
            conn.commit()
    elif skip_open_payments:
        skipped.append("open payments skipped")
    else:
        skipped.append("Open Payments not loaded — extras --download (large CMS CSVs)")

    return {
        "mart_db": mart_db,
        "loaded": loaded,
        "overlays": overlays,
        "skipped": skipped,
        "downloaded": downloaded,
        "open_payments_year": open_payments_year,
        "pat_dt": False,
    }
