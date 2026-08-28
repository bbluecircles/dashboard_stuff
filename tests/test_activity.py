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
    assert "conn.commit()" in source


def test_schema_includes_phase2_columns_and_staging():
    sql = "\n".join(ddl_statements("az_pd"))
    assert "pd_stg_window_claim" in sql
    assert "visits_total" in sql
    assert "panel_average_age" in sql
    names = {name for name, _def in PD_PROVIDER_PHASE2_COLUMNS}
    assert "active_provider" in names
    assert "visits_top_procedure_3" in names
