"""Tests for the Azure catalog fetcher's GPU family and count mapping."""
# pylint: disable=protected-access
import math

import pytest

pytest.importorskip('pandas')
# pylint: disable=wrong-import-position
from sky.catalog.data_fetchers import fetch_azure


def _caps(**values):
    return [{'name': name, 'value': value} for name, value in values.items()]


@pytest.mark.parametrize(
    'family, expected',
    [
        # Pre-existing mappings must be unchanged.
        ('StandardNCASv3_T4Family', 'T4'),
        ('StandardNCadsH100v5Family', 'H100'),
        ('standardNDSH100v5Family', 'H100'),
        # Families added 2026-09: each was present in `az vm list-skus --all`
        # for a real subscription and absent from the catalog.
        ('StandardNCADSA10v4Family', 'A10'),
        ('StandardNCCads2023Family', 'H100'),
        ('standardNDISRH200V5Family', 'H200'),
        ('standardNDISRGB200V6NDRFamily', 'GB200'),
        ('standardNDISRGB300V6Family', 'GB300'),
        ('standardNDISv5MI300XFamily', 'MI300X'),
        # The SKU API sometimes spells families with spaces.
        ('standardNDISR GB300V6 Family', 'GB300'),
        # Non-GPU and FPGA families stay unmapped.
        ('standardDSv3Family', None),
        ('standardNPSFamily', None),
    ],
)
def test_get_gpu_name(family, expected):
    assert fetch_azure.get_gpu_name(family) == expected


def test_parse_capabilities_uses_the_api_gpu_count_when_present():
    gpu_name, gpu_count, vcpus, memory, gen = fetch_azure.parse_capabilities(
        'Standard_ND96isr_H200_v5', 'standardNDISRH200V5Family',
        _caps(GPUs='8', vCPUs='96', MemoryGB='1850', HyperVGenerations='V2'))
    assert (gpu_name, gpu_count, vcpus, memory, gen) == ('H200', '8', 96.0,
                                                         '1850', 'V2')


@pytest.mark.parametrize(
    'instance_type, family, expected_count',
    [
        ('Standard_ND128isr_NDR_GB200_v6', 'standardNDISRGB200V6NDRFamily',
         '4'),
        ('Standard_ND128isr_GB300_v6', 'standardNDISRGB300V6Family', '4'),
        ('Standard_ND96is_MI300X_v5', 'standardNDISv5MI300XFamily', '8'),
    ],
)
def test_parse_capabilities_fills_the_documented_count_when_the_api_has_none(
        instance_type, family, expected_count):
    # These sizes carry no `GPUs` capability in the SKU API (observed
    # 2026-09-02); the documented per-VM count is used instead.
    gpu_name, gpu_count, _, _, _ = fetch_azure.parse_capabilities(
        instance_type, family, _caps(vCPUs='128', MemoryGB='864'))
    assert gpu_name == fetch_azure.get_gpu_name(family)
    assert gpu_count == expected_count


def test_parse_capabilities_never_invents_a_count():
    # A GPU family with no `GPUs` capability AND no documented count is
    # emitted as a plain VM row rather than with a guessed accelerator count.
    gpu_name, gpu_count, _, _, _ = fetch_azure.parse_capabilities(
        'Standard_ND_Fictional_v9', 'standardNDISRH200V5Family',
        _caps(vCPUs='96'))
    assert gpu_name is None
    assert math.isnan(gpu_count)


def test_parse_capabilities_ignores_gpu_capability_on_non_gpu_families():
    # NP-series (FPGA) rows expose a `GPUs`-like capability shape in some API
    # versions; a family that is not mapped must never become a GPU row.
    gpu_name, gpu_count, _, _, _ = fetch_azure.parse_capabilities(
        'Standard_NP10s', 'standardNPSFamily', _caps(GPUs='1', vCPUs='10'))
    assert gpu_name is None
    assert math.isnan(gpu_count)
