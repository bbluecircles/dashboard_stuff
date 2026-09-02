from provider_directory.mart import overlay_cms, overlay_in_system, network_ccn_sql


def test_in_system_uses_pdc_facility_ccn():
    sql = network_ccn_sql("f")
    assert "f.ccn" in sql
    assert "f.facility_type_ccn" in sql
    source = open(overlay_cms.__code__.co_filename, encoding="utf-8").read()
    assert "cms_pdc_facility_affil" in source
    assert "pd_network_npi" in source
    assert "in_system_provider" in source
    assert "overlay_in_system" in source
    flag = open(overlay_in_system.__code__.co_filename, encoding="utf-8").read()
    assert "IF(f.npi IS NOT NULL, 1, 0)" in flag
