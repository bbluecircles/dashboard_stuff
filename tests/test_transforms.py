from provider_directory.transforms import (
    estimated_age,
    is_type1_universe,
    merge_identity,
    normalize_gender,
    normalize_header,
    pdc_identity_score,
    pick_value,
)


def test_estimated_age_happy_path():
    assert estimated_age(1998) == 52
    assert estimated_age(1990) == 60


def test_estimated_age_implausible_or_missing():
    assert estimated_age(None) is None
    assert estimated_age(1949) is None
    assert estimated_age(2025) is None
    assert estimated_age(1950) == 90


def test_dummy_and_type_filters():
    assert is_type1_universe(4, "1", "AZ") is False
    assert is_type1_universe(1234567893, "2", "AZ") is False
    assert is_type1_universe(1234567893, "1", "XX") is False
    assert is_type1_universe(99, "1", "AZ") is False
    assert is_type1_universe(1234567893, "1", "NM") is True


def test_gender_and_headers():
    assert normalize_gender("Female") == "F"
    assert normalize_gender("U") is None
    assert normalize_header("City/Town") == "city_town"
    assert normalize_header("\ufeffNPI") == "npi"


def test_pdc_identity_prefers_arizona_with_school():
    az = pdc_identity_score(state="AZ", med_sch="UNIV OF ARIZONA", grd_yr=2001, gender="F", phone="5205550100")
    other = pdc_identity_score(state="NM", med_sch="OTHER", grd_yr=None, gender=None, phone=None)
    assert az > other


def test_merge_identity_claims_win_names_cms_fills_gaps():
    merged = merge_identity(
        {
            "npi": 1234567893,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "credential": "MD",
            "primary_specialty_code": "207R00000X",
            "primary_specialty_description": "Internal Medicine",
            "specialty_classification": "Internal Medicine",
            "in_system_provider": None,
        },
        {
            "first_name": "A",
            "last_name": "Wrong",
            "gender": "F",
            "medical_school_name": "UNIV OF ARIZONA",
            "graduation_year": 1998,
            "credential": "D.O.",
        },
        {"gender": "M", "last_name": "Nppes"},
    )
    assert merged["first_name"] == "Ada"
    assert merged["last_name"] == "Lovelace"
    assert merged["credential"] == "MD"
    assert merged["gender"] == "F"
    assert merged["gender_source"] == "pdc"
    assert merged["medical_school_name"] == "UNIV OF ARIZONA"
    assert merged["medical_school_graduation_year"] == 1998
    assert merged["estimated_age"] == 52
    assert merged["primary_specialty_code"] == "207R00000X"
    assert merged["name_source"] == "claims"


def test_merge_identity_falls_through_to_nppes():
    merged = merge_identity(
        {"npi": 1234567893, "primary_specialty_code": "207R00000X"},
        {},
        {"first_name": "Ada", "last_name": "Lovelace", "gender": "F", "credential": "MD"},
    )
    assert merged["last_name"] == "Lovelace"
    assert merged["gender"] == "F"
    assert merged["name_source"] == "nppes"
    assert merged["gender_source"] == "nppes"


def test_pick_value_skips_blanks():
    assert pick_value(" ", "NA", "MD") == "MD"
