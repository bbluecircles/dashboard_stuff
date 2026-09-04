import inspect

import pytest

from provider_directory.lookup import list_providers
from provider_directory.settings import market_for_state, parse_state


def test_market_for_state_maps_usps_to_dbs():
    az = market_for_state("AZ")
    assert az.state == "AZ"
    assert az.claims_db == "az"
    assert az.lookup_db == "azal"
    assert az.mart_db == "az_pd"
    tx = market_for_state("tx")
    assert tx.claims_db == "tx"
    assert tx.lookup_db == "txal"
    assert tx.mart_db == "tx_pd"


def test_parse_state_rejects_words():
    with pytest.raises(ValueError):
        parse_state("Arizona")


def test_list_providers_joins_primary_site_only():
    source = inspect.getsource(list_providers)
    assert "site_rank = 1" in source
    assert "pd_provider_practice" in source
    assert "_attach_practices" not in source
