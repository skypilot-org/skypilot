"""Tests for Vast.ai Cloud provider."""

from sky.provision.vast.utils import _create_search_offers_query


def test_search_offers_valid_query():
    got = _create_search_offers_query(instance_type='1x-RTX_5090-32-65536',
                                      region=', CA, NA',
                                      disk_size=32,
                                      secure_only=True)

    assert got == ('chunked=true georegion=true geolocation=NA '
                   'disk_space>=32 num_gpus=1 gpu_name=RTX_5090 '
                   'cpu_ram>=64.0 datacenter=true')
