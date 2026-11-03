"""Reference server copy, overlay and comparison: SQL shape only (no DB)."""

import inspect

from provider_directory import reference as ref
from provider_directory.cli import build_parser
from provider_directory.field_help import catalog, describe_table, tables_in
from provider_directory.schema import PD_PROVIDER_REFERENCE_COLUMNS, TABLES, ddl_statements
from provider_directory.sync import run_sync


def test_reference_reads_are_keyset_paged_and_read_only():
    page = ref.physician_page_sql()
    assert page.startswith("SELECT ")
    assert "`master_ref`.`111_physician`" in page
    assert "WHERE state_abbr = %s AND physician_code > %s ORDER BY physician_code LIMIT %s" in page
    look = ref.physician_lookup_sql([1, 2])
    assert "WHERE physician_code IN (1, 2)" in look
    groups = ref.group_practice_page_sql()
    assert "`Group State Code` = %s OR `Individual State Code` = %s" in groups
    assert "rec_id > %s ORDER BY rec_id LIMIT %s" in groups
    import re

    for sql in (page, look, groups):
        for word in ("INSERT", "UPDATE", "DELETE", "LOCK", "CREATE", "ALTER", "DROP"):
            assert not re.search(rf"\b{word}\b", sql.upper()), (word, sql)
        assert "FOR UPDATE" not in sql.upper()
    source = inspect.getsource(ref)
    assert "get_ref_connection" in source
    # every write targets the mart copy, never the reference database
    for line in source.splitlines():
        if "INSERT" in line or "UPDATE " in line or "RENAME" in line or "DROP" in line:
            assert "_src(" not in line and "REF_DB" not in line, line


def test_copy_swaps_tables_atomically_and_covers_spine_npis():
    copy = inspect.getsource(ref.copy_physicians)
    assert "pd_src_physician_new" in copy
    assert "LEFT JOIN {mart}.pd_src_physician_new s ON s.npi = p.npi" in copy
    assert "WHERE s.npi IS NULL" in copy
    swap = inspect.getsource(ref._swap_in)
    assert "RENAME TABLE {mart}.{table} TO {mart}.{table}_old, {mart}.{table}_new TO {mart}.{table}" in swap
    assert ref.SPINE_LOOKUP_BATCH == 1000
    assert [dst for dst, _s in ref.GROUP_PRACTICE_COLUMNS][:2] == ["rec_id", "group_pac_id"]
    assert dict(ref.GROUP_PRACTICE_COLUMNS)["npi"] == "Individual NPI"


def test_overlay_cleans_placeholders_and_keeps_columns_on_profile():
    sql = ref.overlay_reference_sql("`az_pd`", 3)
    assert "INNER JOIN `az_pd`.pd_src_physician s ON s.npi = p.npi" in sql
    assert "CONCAT('888', CAST(s.npi AS CHAR))" in sql
    assert "REGEXP CONCAT('-', CAST(s.npi AS CHAR), '$')" in sql
    assert "'<UNAVAIL>'" in sql
    assert "p.affiliation_name = " in sql and "p.reference_group_id = " in sql
    assert "WHERE MOD(p.npi, 16) = 3" in sql
    cols = dict(PD_PROVIDER_REFERENCE_COLUMNS)
    for name in ("affiliation_code", "affiliation_name", "affiliation_source", "reference_group_id", "reference_group_name"):
        assert name in cols
    ddl = "\n".join(ddl_statements("az_pd"))
    assert "pd_src_physician" in TABLES and "pd_src_group_practice" in TABLES
    assert "CREATE TABLE IF NOT EXISTS `az_pd`.pd_src_physician" in ddl
    assert "group_pac_id VARCHAR(10)" in ddl


def test_compare_reads_mart_only():
    source = inspect.getsource(ref.reference_compare)
    assert "pd_src_physician" in source and "pd_src_group_practice" in source
    assert "master_ref" not in source and "_src(" not in source
    assert "groups_agree" in source and "groups_differ" in source


def test_sync_ref_clock_and_cli():
    assert "ref" in inspect.signature(run_sync).parameters
    source = inspect.getsource(run_sync)
    assert "copy_reference_tables" in source and "overlay_reference" in source
    args = build_parser().parse_args(["sync", "--ref", "--state", "AZ_CMS"])
    assert args.ref is True and args.claims is False
    assert build_parser().parse_args(["ref-compare", "--state", "AZ_CMS"]).func.__name__ == "_cmd_ref_compare"


def test_field_help_knows_the_reference_tables():
    assert tables_in("master_ref.111_physician via pd_src_physician") == ["ref.111_physician", "pd_src_physician"]
    assert describe_table("ref.111_physician")["name"] == "master_ref.111_physician"
    cat = catalog(dev=True)["provider"]
    assert cat["affiliation_name"]["label"] == "Affiliation"
    assert "pd_src_physician" in cat["affiliation_name"]["tables"]
    assert "ref.111_physician" in cat["affiliation_name"]["tables"]
