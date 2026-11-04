from provider_directory.activity import iter_period_codes, rebuild_activity, reset_activity_columns_sql
from provider_directory.schema import PD_PROVIDER_PHASE2_COLUMNS, ddl_statements
from provider_directory.transforms import (
    age_band,
    is_active,
    summarize_panel,
    top_n_codes,
)


def test_age_bands():
    assert age_band(0) == "0_19"
    assert age_band(19) == "0_19"
    assert age_band(20) == "20_44"
    assert age_band(44) == "20_44"
    assert age_band(65) == "65_84"
    assert age_band(85) == "85_plus"
    assert age_band(120) == "85_plus"
    assert age_band(999) is None
    assert age_band(-1) is None


def test_summarize_panel_average_and_percents():
    stats = summarize_panel(
        [
            {"age": 10, "gender": "F"},
            {"age": 30, "gender": "F"},
            {"age": 50, "gender": "M"},
            {"age": 70, "gender": "M"},
            {"age": 90, "gender": "F"},
        ]
    )
    assert stats["panel_size"] == 5
    assert stats["panel_average_age"] == 50.0
    assert stats["panel_percent_age_0_19"] == 20.0
    assert stats["panel_percent_age_20_44"] == 20.0
    assert stats["panel_percent_age_45_64"] == 20.0
    assert stats["panel_percent_age_65_84"] == 20.0
    assert stats["panel_percent_age_85_plus"] == 20.0
    assert stats["panel_percent_female"] == 60.0
    assert stats["panel_percent_male"] == 40.0


def test_summarize_panel_empty():
    stats = summarize_panel([])
    assert stats["panel_size"] == 0
    assert stats["panel_average_age"] is None
    assert stats["panel_percent_female"] is None


def test_top_n_codes_stable_tiebreak():
    assert top_n_codes({"J449": 10, "E119": 10, "I10": 3, "": 99}, n=3) == ["E119", "J449", "I10"]


def test_active_flag():
    assert is_active(12, 0) is True
    assert is_active(0, 4) is True
    assert is_active(0, 0) is False
    assert is_active(None, None) is False


def test_iter_period_codes_frozen_window():
    assert iter_period_codes(202308, 202407) == [
        202308,
        202309,
        202310,
        202311,
        202312,
        202401,
        202402,
        202403,
        202404,
        202405,
        202406,
        202407,
    ]


def test_phase2_sql_uses_frozen_window_and_mart_only():
    sql = reset_activity_columns_sql("az_pd")
    assert "`az_pd`.pd_provider" in sql
    assert "az.pat_dt" not in sql
    source = open(rebuild_activity.__code__.co_filename, encoding="utf-8").read()
    assert "period_code = %s" in source
    assert "MOD(IFNULL(pat_id, 0)" in source
    assert "INSERT INTO" in source
    assert "az.physician" not in source
    assert "COLLATE {MART_COLLATION}" in source


def test_schema_includes_phase2_columns_and_staging():
    sql = "\n".join(ddl_statements("az_pd"))
    assert "pd_stg_window_claim" in sql
    assert "visits_total" in sql
    assert "panel_average_age" in sql
    names = {name for name, _def in PD_PROVIDER_PHASE2_COLUMNS}
    assert "active_provider" in names
    assert "visits_top_procedure_3" in names


def test_encounter_id_is_signed_and_buckets_use_abs():
    # az_cms.pat_dt.encounter_id is decimal(65,0) with 22-digit ids, past any
    # BIGINT (error 1264 at Phase 2 load). Staging must use a wide DECIMAL and
    # bucket by ABS so a negative id is never skipped by MOD(x, N) = 0..N-1.
    import re
    from provider_directory import activity, analytics, complete, locations

    sql = "\n".join(ddl_statements("az_pd"))
    assert "encounter_id BIGINT" not in sql
    assert sql.count("encounter_id DECIMAL(25,0)") == 4
    assert "pat_id BIGINT" not in sql
    assert sql.count("pat_id DECIMAL(25,0)") == 3
    for table in ("pd_stg_window_claim", "pd_stg_visit", "pd_stg_visit_site", "pd_stg_visit_date"):
        assert table in sql
    for mod in (activity, analytics, complete, locations):
        source = open(mod.__file__, encoding="utf-8").read()
        for match in re.findall(r"MOD\(([^,]+encounter_id[^,]*),\s*\{VISIT_BUCKETS\}\)", source):
            assert match.startswith("ABS("), f"{mod.__name__}: {match}"


def test_top_codes_rank_five_and_primary_procedures_filter_before_ranking():
    import inspect

    from provider_directory.activity import (
        rebuild_top_codes,
        reset_activity_columns_sql,
        top_code_overlay_sql,
        top_code_statements,
    )
    from provider_directory.cli import build_parser
    from provider_directory.pipeline import run_phase2
    from provider_directory.schema import PD_PROVIDER_PHASE2_COLUMNS, TOP_CODE_TABLES
    from provider_directory.settings import TOP_N
    from provider_directory.transforms import primary_procedure_filter_sql, strict_work_rvu_sql

    assert TOP_N == 5
    st = top_code_statements("`az_pd`", "`azal`")
    assert set(st) == set(TOP_CODE_TABLES)
    for sql in st.values():
        assert "WHERE ranked.rk <= 5" in sql
        assert "%" not in sql.replace("%%", "").replace("%s", "")
    primary = st["pd_stg_top_primary_px"]
    assert "INNER JOIN `azal`.procd pr" in primary
    assert "> 1.0" in primary
    assert "BETWEEN '99202' AND '99499'" in primary
    assert "BETWEEN '98000' AND '98016'" in primary
    assert "BETWEEN '92002' AND '92014'" in primary
    assert "BETWEEN '90785' AND '90899'" in primary
    assert "BETWEEN '97161' AND '97168'" in primary
    assert "'G0439'" in primary and "'G0463'" in primary and "'G2025'" in primary and "'99024'" in primary
    assert "BETWEEN '92521' AND '92524'" in primary
    assert "REGEXP '^[0-9]{4}F$'" in primary and "REGEXP '^[0-9]{4}U$'" in primary and "REGEXP '^C[0-9]{4}$'" in primary
    assert "nf_total_rvu" not in strict_work_rvu_sql("pr")
    assert "NOT" in primary_procedure_filter_sql("v.px", "pr")
    joins, sets = top_code_overlay_sql("`az_pd`")
    assert "pd_stg_top_primary_px" in joins
    assert "p.visits_top_primary_procedure_5_name = t_primary_px.c5_name" in sets
    assert "p.visits_top_diagnosis_4 = t_dx.c4" in sets
    reset = reset_activity_columns_sql("az_pd", bucket=0)
    assert "visits_top_primary_procedure_1 = NULL" in reset and "visits_top_procedure_5_name = NULL" in reset
    cols = dict(PD_PROVIDER_PHASE2_COLUMNS)
    assert "visits_top_diagnosis_5_name" in cols and "visits_top_primary_procedure_3" in cols
    source = inspect.getsource(rebuild_top_codes)
    body = source[source.index('"""', source.index('"""') + 3) + 3:]  # after the docstring
    assert "TRUNCATE TABLE" in body and "pd_stg_window_claim" not in body and "load_window_claims" not in body
    assert "top_codes_only" in inspect.getsource(run_phase2)
    assert build_parser().parse_args(["phase2", "--top-codes-only"]).top_codes_only is True
