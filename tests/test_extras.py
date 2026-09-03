import inspect
from io import StringIO

from provider_directory.cli import build_parser
from provider_directory.cms import CatalogError, open_payments_detail_urls
from provider_directory.cms.parse import (
    iter_csv_rows,
    parse_mips_row,
    parse_open_payments_row,
    parse_pdc_clinician_row,
    parse_utilization_row,
)
from provider_directory.extras import (
    _load_open_payments_from_paths,
    accumulate_open_payments,
    known_pos_sql,
    open_payments_insert_rows,
    open_payments_year_from_name,
    overlay_em,
    overlay_pos,
    pos_in_sql,
    pos_mix_bucket_sql,
    px_in_sql,
    rebuild_extras,
)
from provider_directory.schema import PD_PROVIDER_EXTRAS_COLUMNS, TABLES, ddl_statements
from provider_directory.settings import (
    ESTABLISHED_PX,
    NEW_PATIENT_PX,
    PDC_MIPS_DATASET,
    PDC_UTILIZATION_DATASET,
    POS_TELEHEALTH,
)
from provider_directory.transforms import parse_money, parse_yes_no


def test_extras_cli_flags():
    args = build_parser().parse_args(
        ["extras", "--download", "--reload-pdc", "--skip-open-payments", "--year", "2024"]
    )
    assert args.cmd == "extras"
    assert args.download is True
    assert args.reload_pdc is True
    assert args.skip_open_payments is True
    assert args.year == 2024


def test_schema_includes_extras():
    sql = "\n".join(ddl_statements("az_pd"))
    names = {name for name, _def in PD_PROVIDER_EXTRAS_COLUMNS}
    assert "group_size" in names
    assert "visits_percent_telehealth" in names
    assert "visits_percent_inpatient" in names
    assert "open_payments_general_total" in names
    assert "sec_spec_1" in sql
    assert "cms_pdc_mips" in sql
    assert "pd_provider_utilization" in sql
    assert "cms_pdc_mips" in TABLES
    assert "pd_provider_utilization" in TABLES


def test_parse_pdc_secondary_specialty():
    rows = list(
        iter_csv_rows(
            StringIO(
                "NPI,pri_spec,Sec_spec_1,Sec_spec_2,telehlth,num_org_mem,State\n"
                "1234567893,CARDIOLOGY,INTERNAL MEDICINE,,Y,1200,AZ\n"
            )
        )
    )
    parsed = parse_pdc_clinician_row(rows[0])
    assert parsed["sec_spec_1"] == "INTERNAL MEDICINE"
    assert parsed["sec_spec_2"] is None
    assert parsed["telehlth"] == "Y"
    assert parsed["num_org_mem"] == 1200


def test_parse_mips_utilization_open_payments():
    mips = parse_mips_row(
        {"npi": "1234567893", "org_pac_id": "0042345678", "final_mips_score": "91.2", "quality_mips_score": "88"}
    )
    assert mips["npi"] == 1234567893
    assert mips["final_score"] == 91.2
    assert mips["quality_score"] == 88.0
    util = parse_utilization_row(
        {
            "npi": "1234567893",
            "procedure_category": "Pacemaker insertion or repair",
            "count": "1-10",
            "percentile": "71",
            "profile_display_indicator": "Y",
        }
    )
    assert util["count_label"] == "1-10"
    assert util["percentile"] == 71
    pay = parse_open_payments_row(
        {"covered_recipient_npi": "1234567893", "total_amount_of_payment_usdollars": "$1,234.50"}
    )
    assert pay["amount"] == 1234.5
    assert parse_open_payments_row({"covered_recipient_npi": "", "total_amount_of_payment_usdollars": "10"}) is None


def test_money_and_yes_no():
    assert parse_money("1,000.25") == 1000.25
    assert parse_yes_no("Y") == 1
    assert parse_yes_no("n") == 0
    assert parse_yes_no("maybe") is None


def test_em_and_pos_sql_use_staging_not_pat_dt():
    em_sql = inspect.getsource(overlay_em)
    pos_sql = inspect.getsource(overlay_pos)
    assert "pat_dt" not in em_sql
    assert "pat_dt" not in pos_sql
    assert "pd_stg_visit" in em_sql
    assert "pd_stg_visit_site" in pos_sql
    new_sql = px_in_sql("v", NEW_PATIENT_PX)
    est_sql = px_in_sql("v", ESTABLISHED_PX)
    assert "99202" in new_sql
    assert "99215" in est_sql
    tele = pos_in_sql("sl", POS_TELEHEALTH)
    assert "2" in tele and "10" in tele
    known = known_pos_sql("sl")
    assert "11" in known and "22" in known and "24" in known and "23" in known
    assert "21" in known and "81" in known
    mix = pos_mix_bucket_sql("sl")
    assert "inpatient" in mix
    assert "Independent Laboratory" in mix
    assert "Short Term Acute Care Hospital" in mix


def test_dataset_ids():
    assert PDC_MIPS_DATASET == "a174-a962"
    assert PDC_UTILIZATION_DATASET == "n0yb-util"


def test_open_payments_catalog_picks_latest_complete_year():
    items = [
        {
            "title": "2024 General Payment Data",
            "keyword": ["2024"],
            "theme": ["General Payments"],
            "distribution": [{"mediaType": "text/csv", "downloadURL": "https://example.test/g2024.csv"}],
        },
        {
            "title": "2024 Research Payment Data",
            "keyword": ["2024"],
            "theme": ["Research Payments"],
            "distribution": [{"format": "csv", "downloadURL": "https://example.test/r2024.csv"}],
        },
        {
            "title": "2024 Ownership Payment Data",
            "keyword": ["2024"],
            "theme": ["Ownership Payments"],
            "distribution": [{"mediaType": "text/csv", "downloadURL": "https://example.test/o2024.csv"}],
        },
        {
            "title": "2025 General Payment Data",
            "keyword": ["2025"],
            "theme": ["General Payments"],
            "distribution": [{"mediaType": "text/csv", "downloadURL": "https://example.test/g2025.csv"}],
        },
        {
            "title": "2025 Research Payment Data",
            "keyword": ["2025"],
            "theme": ["Research Payments"],
            "distribution": [{"mediaType": "text/csv", "downloadURL": "https://example.test/r2025.csv"}],
        },
        {
            "title": "2025 Ownership Payment Data",
            "keyword": ["2025"],
            "theme": ["Ownership Payments"],
            "distribution": [{"mediaType": "text/csv", "downloadURL": "https://example.test/o2025.csv"}],
        },
    ]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return items

    class FakeSession:
        def get(self, url, timeout=60):
            assert "openpaymentsdata.cms.gov" in url
            return FakeResponse()

    year, urls = open_payments_detail_urls(session=FakeSession())
    assert year == 2025
    assert urls["general"].endswith("g2025.csv")
    year, urls = open_payments_detail_urls(2024, session=FakeSession())
    assert year == 2024
    assert urls["research"].endswith("r2024.csv")
    try:
        open_payments_detail_urls(2010, session=FakeSession())
        raise AssertionError("expected CatalogError")
    except CatalogError:
        pass


def test_accumulate_open_payments_filters_spine():
    totals = {}
    rows = [
        {"covered_recipient_npi": "1234567893", "total_amount_of_payment_usdollars": "10"},
        {"covered_recipient_npi": "1234567893", "total_amount_of_payment_usdollars": "2.5"},
        {"covered_recipient_npi": "1111111112", "total_amount_of_payment_usdollars": "999"},
    ]
    kept = accumulate_open_payments(rows, {1234567893}, kind="general", totals=totals)
    assert kept == 2
    assert totals[1234567893]["general"][0] == 12.5
    assert 1111111112 not in totals
    insert_rows = open_payments_insert_rows(totals, 2024)
    assert insert_rows[0]["payment_kind"] == "general"
    assert insert_rows[0]["total"] == 12.5
    assert insert_rows[0]["payment_count"] == 2


def test_open_payments_reads_cached_csv_not_http_wrapper():
    assert open_payments_year_from_name("OP_DTL_GNRL_PGYR2025_P06302026_06032026.csv") == 2025
    source = inspect.getsource(_load_open_payments_from_paths)
    assert "iter_local_csv" in source
    assert "iter_http_csv" not in source
    assert "iter_http_csv" not in inspect.getsource(rebuild_extras)
    source = inspect.getsource(rebuild_extras)
    assert "Never scans pat_dt" in source
    assert "az.pat_dt" not in source
    assert "FROM az.pat_dt" not in inspect.getsource(overlay_em)
    assert "FROM az.pat_dt" not in inspect.getsource(overlay_pos)
