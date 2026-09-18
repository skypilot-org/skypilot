"""Tests for the OCI catalog fetcher (public estimator + price list path)."""
import ast
import csv
import os

import pytest

from sky.catalog.data_fetchers import fetch_oci


def _product(part_number, usd, metric):
    return {
        'partNumber': part_number,
        'displayName': part_number,
        'metricName': metric,
        'currencyCodeLocalizations': [
            {
                'currencyCode': 'EUR',
                'prices': [{
                    'model': 'PAY_AS_YOU_GO',
                    'value': usd * 0.9
                }]
            },
            {
                'currencyCode': 'USD',
                'prices': [{
                    'model': 'PAY_AS_YOU_GO',
                    'value': usd
                }]
            },
        ],
    }


def _gpu_shape(name,
               part_number,
               gpu_qty,
               gpu_memory_qty,
               ocpus,
               memory,
               preemptible=False,
               status='ACTIVE',
               hidden=False):
    return {
        'name': name,
        'status': status,
        'hidden': hidden,
        'shapeType': {
            'value': 'bm'
        },
        'subType': {
            'value': 'gpu'
        },
        'gpuQty': gpu_qty,
        'gpuMemoryQty': gpu_memory_qty,
        'bundleMemoryQty': memory,
        'allowPreemptible': preemptible,
        'products': [{
            'type': {
                'value': 'ocpu'
            },
            'partNumber': part_number,
            'qty': ocpus,
        }],
    }


def _flex_shape(name, ocpu_part, memory_part, max_ocpus=64, max_memory=1024):
    return {
        'name': name,
        'status': 'ACTIVE',
        'hidden': False,
        'shapeType': {
            'value': 'vm'
        },
        'subType': {
            'value': 'flexible'
        },
        'gpuQty': None,
        'bundleMemoryQty': 0,
        'allowPreemptible': True,
        'products': [
            {
                'type': {
                    'value': 'ocpu'
                },
                'partNumber': ocpu_part,
                'qty': 0,
                'min': 1,
                'max': max_ocpus,
            },
            {
                'type': {
                    'value': 'memory'
                },
                'partNumber': memory_part,
                'qty': 0,
                'min': 1,
                'max': max_memory,
            },
        ],
    }


PRODUCTS = [
    _product('B98415', 10.0, 'GPU Per Hour'),  # H100
    _product('B110979', 16.0, 'GPU Per Hour'),  # GB200
    _product('B109485', 6.0, 'GPU Per Hour'),  # MI300X
    _product('B89734', 2.95, 'GPU Per Hour'),  # V100 (GPU3 shapes)
    _product('B93113', 0.025, 'OCPU Per Hour'),  # E4 OCPU
    _product('B93114', 0.0015, 'Gigabyte Per Hour'),  # E4 memory
]

SHAPES = {
    'items': [
        _gpu_shape('BM.GPU.H100.8', 'B98415', 8, 640, 112, 2048),
        # Display-only suffix, wrong OCPU count and Grace (Arm) CPUs: the
        # override table must win.
        _gpu_shape('BM.GPU.GB200.4 (NVL72)', 'B110979', 4, 756, 128, 960),
        _gpu_shape('BM.GPU.MI300X.8', 'B109485', 8, 1536, 112, 2048),
        _gpu_shape('VM.GPU3.1', 'B89734', 1, 16, 6, 90, preemptible=True),
        _gpu_shape('BM.GPU.OLD.8',
                   'B98415',
                   8,
                   640,
                   112,
                   2048,
                   status='RETIRED'),
        _gpu_shape('BM.GPU.HIDDEN.8', 'B98415', 8, 640, 112, 2048, hidden=True),
        _gpu_shape('BM.GPU.NOPRICE.8', 'B000000', 8, 640, 112, 2048),
        _flex_shape('VM.Standard.E4.Flex', 'B93113', 'B93114'),
        # Not in CPU_FLEX_SHAPES: ignored.
        _flex_shape('VM.Standard.E3.Flex', 'B93113', 'B93114'),
        # Not a compute shape: ignored.
        _flex_shape('PostgreSQL.VM.Standard.E4.Flex', 'B93113', 'B93114'),
    ]
}


@pytest.mark.parametrize('shape,expected', [
    ('BM.GPU.H100.8', 'H100'),
    ('BM.GPU.H200.8', 'H200'),
    ('BM.GPU.L40S.4', 'L40S'),
    ('BM.GPU.A10.4', 'A10'),
    ('VM.GPU.A10.1', 'A10'),
    ('BM.GPU.A100-v2.8', 'A100-80GB'),
    ('BM.GPU4.8', 'A100'),
    ('BM.GPU3.8', 'V100'),
    ('VM.GPU2.1', 'P100'),
    ('BM.GPU.B200.8', 'B200'),
    ('BM.GPU.GB200.4', 'GB200'),
    ('BM.GPU.GB300.4', 'GB300'),
    ('BM.GPU.MI300X.8', 'MI300X'),
    ('BM.GPU.MI355X.8', 'MI355X'),
    ('BM.GPU.RTXPRO.8', 'RTXPRO6000'),
    ('VM.Standard.E4.Flex', None),
    ('BM.Standard.E5.192', None),
])
def test_accelerator_name_from_shape(shape, expected):
    assert fetch_oci.accelerator_name_from_shape(shape) == expected


def test_normalize_shape_name_strips_display_suffix():
    assert fetch_oci.normalize_shape_name(
        'BM.GPU.GB200.4 (NVL72)') == 'BM.GPU.GB200.4'
    assert fetch_oci.normalize_shape_name('BM.GPU.H100.8') == 'BM.GPU.H100.8'


def test_gpu_manufacturer():
    assert fetch_oci.gpu_manufacturer('MI300X') == 'AMD'
    assert fetch_oci.gpu_manufacturer('MI355X') == 'AMD'
    assert fetch_oci.gpu_manufacturer('H100') == 'NVIDIA'
    assert fetch_oci.gpu_manufacturer('GB200') == 'NVIDIA'


def test_load_prices_picks_usd_pay_as_you_go():
    prices = fetch_oci.load_prices(PRODUCTS)
    assert prices['B98415'] == 10.0
    assert prices['B93114'] == 0.0015
    assert 'B000000' not in prices


def test_collect_shapes():
    shapes = {
        s.instance_type: s for s in fetch_oci.collect_shapes(SHAPES, PRODUCTS)
    }
    gpu_shapes = sorted(s.shape for s in shapes.values() if s.is_gpu)
    assert gpu_shapes == [
        'BM.GPU.GB200.4', 'BM.GPU.H100.8', 'BM.GPU.MI300X.8', 'VM.GPU3.1'
    ]
    for skipped in ('BM.GPU.OLD.8', 'BM.GPU.HIDDEN.8', 'BM.GPU.NOPRICE.8'):
        assert skipped not in shapes

    # Price = GPUs x per-GPU price; vCPUs = 2 x OCPUs on x86; no spot price
    # unless the shape is offered as preemptible.
    h100 = shapes['BM.GPU.H100.8']
    assert (h100.vcpus, h100.memory_gib, h100.price,
            h100.spot_price) == (224, 2048, 80.0, None)
    assert (h100.accelerator_name, h100.accelerator_count, h100.gpu_memory_mib,
            h100.manufacturer) == ('H100', 8, 81920, 'NVIDIA')
    assert ast.literal_eval(h100.gpu_info()) == {
        'Gpus': [{
            'Name': 'H100',
            'Manufacturer': 'NVIDIA',
            'Count': 8,
            'MemoryInfo': {
                'SizeInMiB': 81920
            },
        }],
        'TotalGpuMemoryInMiB': 655360,
    }

    # Override table: 144 Grace cores without SMT, 960 GB, display suffix
    # removed from the shape name, per-GPU memory from GPU_MEMORY_GB.
    gb200 = shapes['BM.GPU.GB200.4']
    assert gb200.shape == 'BM.GPU.GB200.4'
    assert (gb200.vcpus, gb200.memory_gib, gb200.price) == (144, 960, 64.0)
    assert gb200.gpu_memory_mib == 192 * 1024

    mi300x = shapes['BM.GPU.MI300X.8']
    assert mi300x.manufacturer == 'AMD'
    assert (mi300x.price, mi300x.gpu_memory_mib) == (48.0, 192 * 1024)

    v100 = shapes['VM.GPU3.1']
    assert (v100.vcpus, v100.price, v100.spot_price) == (12, 2.95, 1.475)

    # Flexible CPU shapes: one entry per (vCPU, memory) point, priced as
    # OCPUs x OCPU price + GB x memory price, 50% off when preemptible.
    small = shapes['VM.Standard.E4.Flex$_2_8']
    assert (small.vcpus, small.memory_gib) == (2, 8)
    assert small.price == pytest.approx(0.025 + 8 * 0.0015)
    assert small.spot_price == pytest.approx(small.price / 2)
    assert not small.is_gpu and small.gpu_info() == ''
    flex = [s for s in shapes.values() if s.shape == 'VM.Standard.E4.Flex']
    assert len(flex) == len(fetch_oci.FLEX_VCPUS) * len(
        fetch_oci.FLEX_MEMORY_PER_VCPU_GIB)
    assert not any(s.shape == 'VM.Standard.E3.Flex' for s in shapes.values())


def test_flex_shape_respects_product_limits():
    shape = _flex_shape('VM.Standard.E4.Flex',
                        'B93113',
                        'B93114',
                        max_ocpus=8,
                        max_memory=64)
    infos = fetch_oci.flex_shape_infos(shape, fetch_oci.load_prices(PRODUCTS))
    assert max(i.vcpus for i in infos) == 16
    assert max(i.memory_gib for i in infos) == 64


def test_static_availability_expands_multi_ad_regions():
    zones = fetch_oci.static_availability({'us-ashburn-1': 3, 'ap-tokyo-1': 1})
    assert zones == {
        'ap-tokyo-1': ['ap-tokyo-1-AD-1'],
        'us-ashburn-1': [
            'us-ashburn-1-AD-1', 'us-ashburn-1-AD-2', 'us-ashburn-1-AD-3'
        ],
    }


def test_regions_table_is_sane():
    # Every region identifier looks like a real OCI region and has 1-3 ADs.
    for region, num_ads in fetch_oci.REGIONS.items():
        assert region.count('-') == 2 and region.split('-')[-1].isdigit()
        assert 1 <= num_ads <= 3
    # The regions the previous hand-maintained catalog covered must remain.
    for region in ('us-ashburn-1', 'us-phoenix-1', 'eu-frankfurt-1',
                   'uk-london-1', 'ap-tokyo-1', 'sa-saopaulo-1'):
        assert region in fetch_oci.REGIONS


def test_expand_rows_and_write_csv(tmp_path):
    shapes = fetch_oci.collect_shapes(SHAPES, PRODUCTS)
    availability = {
        'us-ashburn-1': {
            'us-ashburn-1-AD-1': None,
            'us-ashburn-1-AD-2': {'BM.GPU.H100.8', 'VM.Standard.E4.Flex'},
        },
    }
    rows = fetch_oci.expand_rows(shapes, availability)
    ad1 = [r for r in rows if r['AvailabilityZone'] == 'us-ashburn-1-AD-1']
    ad2 = [r for r in rows if r['AvailabilityZone'] == 'us-ashburn-1-AD-2']
    # None -> every shape; a set -> only the listed shapes (flex sizes map
    # back to their base shape name).
    assert len(ad1) == len(shapes)
    assert {r['InstanceType'].split('$_')[0] for r in ad2
           } == {'BM.GPU.H100.8', 'VM.Standard.E4.Flex'}

    output = os.path.join(str(tmp_path), 'oci', 'vms.csv')
    fetch_oci.write_csv(rows, output)
    with open(output, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == [
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'GpuInfo', 'Price', 'SpotPrice', 'Region',
            'AvailabilityZone'
        ]
        written = list(reader)
    h100 = next(r for r in written if r['InstanceType'] == 'BM.GPU.H100.8' and
                r['AvailabilityZone'] == 'us-ashburn-1-AD-1')
    assert h100['Price'] == '80' and h100['SpotPrice'] == ''
    assert h100['AcceleratorCount'] == '8' and h100['vCPUs'] == '224'
    flex = next(
        r for r in written if r['InstanceType'] == 'VM.Standard.E4.Flex$_2_8')
    assert (flex['Price'], flex['SpotPrice']) == ('0.037', '0.0185')
    assert flex['AcceleratorName'] == '' and flex['GpuInfo'] == ''


def test_format_price():
    assert fetch_oci._format_price(None) == ''  # pylint: disable=protected-access
    assert fetch_oci._format_price(2.0) == '2'  # pylint: disable=protected-access
    assert fetch_oci._format_price(0.0185) == '0.0185'  # pylint: disable=protected-access
    assert fetch_oci._format_price(68.8) == '68.8'  # pylint: disable=protected-access
