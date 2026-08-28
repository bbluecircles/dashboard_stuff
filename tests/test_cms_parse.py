from io import StringIO

from provider_directory.cms.parse import (
    better_pdc_identity,
    iter_csv_rows,
    keep_nppes_row,
    keep_pdc_clinician_row,
    parse_facility_row,
    parse_nppes_row,
    parse_pdc_clinician_row,
    pdc_row_as_identity,
)
from provider_directory.transforms import nppes_primary_taxonomy


PDC_CSV = """NPI,Ind_PAC_ID,Provider Last Name,Provider First Name,suff,gndr,Cred,Med_sch,Grd_yr,pri_spec,Telephone Number,State,ZIP Code,adrs_id,org_pac_id
1234567893,111,SMITH,JANE,,F,MD,UNIV OF ARIZONA,1998,INTERNAL MEDICINE,5205550100,AZ,85701,AZ1,999
1234567893,111,SMITH,JANE,,F,MD,OTHER,,INTERNAL MEDICINE,,NM,88001,NM1,999
1111111112,222,OUT,OFSTATE,,M,DO,SOME SCHOOL,2000,FAMILY PRACTICE,,CA,90210,CA1,888
"""


def test_parse_pdc_and_keep_spine_or_az():
    rows = list(iter_csv_rows(StringIO(PDC_CSV)))
    parsed = [parse_pdc_clinician_row(r) for r in rows]
    assert parsed[0]["npi"] == 1234567893
    assert parsed[0]["gender"] == "F"
    assert parsed[0]["grd_yr"] == 1998
    assert parsed[0]["state"] == "AZ"
    spine = {1234567893}
    kept = [row for row in parsed if keep_pdc_clinician_row(row, spine)]
    assert [row["adrs_id"] for row in kept] == ["AZ1", "NM1"]


def test_collapse_prefers_az_school_row():
    rows = [parse_pdc_clinician_row(r) for r in iter_csv_rows(StringIO(PDC_CSV))]
    best = None
    for row in rows[:2]:
        best = better_pdc_identity(best, row)
    assert best["state"] == "AZ"
    assert best["med_sch"] == "UNIV OF ARIZONA"
    ident = pdc_row_as_identity(best)
    assert ident["graduation_year"] == 1998


def test_facility_ccn():
    row = parse_facility_row(
        {
            "npi": "1234567893",
            "facility_type": "Hospital",
            "facility_affiliations_certification_number": "030102",
        }
    )
    assert row["ccn"] == "030102"


def test_nppes_type1_and_primary_taxonomy():
    org = parse_nppes_row({"npi": "1234567893", "entity_type_code": "2"})
    assert org is None
    raw = {
        "npi": "1234567893",
        "entity_type_code": "1",
        "provider_last_name_legal_name": "SMITH",
        "provider_first_name": "JANE",
        "provider_sex_code": "F",
        "provider_credential_text": "MD",
        "provider_business_practice_location_address_state_name": "AZ",
        "healthcare_provider_taxonomy_code_1": "207Q00000X",
        "healthcare_provider_primary_taxonomy_switch_1": "N",
        "healthcare_provider_taxonomy_code_2": "207R00000X",
        "healthcare_provider_primary_taxonomy_switch_2": "Y",
        "last_update_date": "08/01/2026",
        "is_sole_proprietor": "N",
    }
    parsed = parse_nppes_row(raw)
    assert parsed["gender"] == "F"
    assert parsed["primary_taxonomy"] == "207R00000X"
    assert parsed["practice_state"] == "AZ"
    assert parsed["last_update_date"] == "2026-08-01"
    assert nppes_primary_taxonomy(raw) == "207R00000X"
    assert keep_nppes_row(parsed, spine_npis=set()) is True
    parsed["practice_state"] = "CA"
    parsed["mailing_state"] = "NV"
    assert keep_nppes_row(parsed, spine_npis=set()) is False
    assert keep_nppes_row(parsed, spine_npis={1234567893}) is True
