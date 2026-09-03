from provider_directory.cms import nppes_monthly_zip_url, pdc_csv_url
from provider_directory.spine import spine_select_sql
from provider_directory.schema import TABLES, ddl_statements


def test_nppes_url_picks_monthly_v2():
    html = """
    <a href="./NPPES_Data_Dissemination_August_2026_V2.zip">monthly</a>
    <a href="./NPPES_Deactivated_NPI_Report_081026_V2.zip">deact</a>
    <a href="./NPPES_Data_Dissemination_081726_082326_Weekly_V2.zip">weekly</a>
    """
    url = nppes_monthly_zip_url(html=html)
    assert url.endswith("NPPES_Data_Dissemination_August_2026_V2.zip")
    assert "Weekly" not in url


def test_pdc_csv_url_reads_nested_metastore(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "distribution": [
                    {
                        "data": {
                            "mediaType": "text/csv",
                            "downloadURL": "https://example.test/DAC_NationalDownloadableFile.csv",
                        }
                    }
                ]
            }

    class FakeSession:
        def __init__(self):
            self.urls = []

        def get(self, url, timeout=60):
            self.urls.append(url)
            return FakeResponse()

    session = FakeSession()
    assert pdc_csv_url("mj5m-pzi6", session=session).endswith("DAC_NationalDownloadableFile.csv")
    assert pdc_csv_url("a174-a962", session=session)
    assert pdc_csv_url("n0yb-util", session=session)
    assert any("a174-a962" in url for url in session.urls)
    assert any("n0yb-util" in url for url in session.urls)


def test_spine_sql_excludes_dummies_and_joins_azal():
    sql = spine_select_sql()
    assert "npi_type = '1'" in sql
    assert "NOT IN (0, 4)" in sql
    assert "state_abbr <> 'XX'" in sql
    assert "npi_spec_grp" in sql


def test_schema_has_locked_tables():
    sql = "\n".join(ddl_statements("az_pd"))
    for table in TABLES:
        assert table in sql
    assert "pd_network_npi" in sql
    assert "in_system_provider" in sql
    assert "visits_total" in sql
    assert "utf8mb4_unicode_520_ci" in sql
    assert "pd_stg_visit" in sql
    assert "cms_pdc_mips" in sql
    assert "pd_provider_utilization" in sql
