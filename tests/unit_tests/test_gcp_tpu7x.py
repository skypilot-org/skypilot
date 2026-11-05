"""Tests for TPU7x on GCP Compute Engine machine types."""
import io
import textwrap
from unittest.mock import patch

import pandas as pd
import pytest

from sky import resources as resources_lib
from sky.catalog import gcp_catalog
from sky.clouds import Region
from sky.clouds import Zone
from sky.clouds.gcp import GCP
from sky.clouds.utils import gcp_utils
from sky.provision.gcp import instance_utils
from sky.provision.gcp import volume_utils as gcp_volume_utils
from sky.utils import accelerator_registry
from sky.utils import resources_utils

# Rows in the format produced by sky/catalog/data_fetchers/fetch_gcp.py.
_TPU7X_CATALOG_ROWS = textwrap.dedent("""\
    InstanceType,vCPUs,MemoryGiB,AcceleratorName,AcceleratorCount,GpuInfo,Region,AvailabilityZone,Price,SpotPrice
    tpu7x-standard-4t,224.0,960.0,,,,us-central1,us-central1-c,0.0,0.0
    tpu7x-standard-4t,224.0,960.0,,,,us-east5,us-east5-a,0.0,0.0
    ,,,tpu7x,4,"{'Gpus': [{'MemoryInfo': {'SizeInMiB': 196608}}]}",us-central1,us-central1-c,48.0,16.4616
    ,,,tpu7x,4,"{'Gpus': [{'MemoryInfo': {'SizeInMiB': 196608}}]}",us-east5,us-east5-a,48.0,21.12
    """)


@pytest.fixture
def tpu7x_catalog(monkeypatch):
    """Adds TPU7x rows to the GCP catalog, replacing any existing ones."""
    df = gcp_catalog._df._load_df()  # pylint: disable=protected-access
    is_tpu7x = ((df['InstanceType'].fillna('').str.startswith('tpu7x-')) |
                (df['AcceleratorName'].fillna('') == 'tpu7x'))
    tpu7x_df = pd.read_csv(io.StringIO(_TPU7X_CATALOG_ROWS),
                           dtype={'AcceleratorName': str})
    monkeypatch.setattr(gcp_catalog, '_df',
                        pd.concat([df[~is_tpu7x], tpu7x_df], ignore_index=True))


def test_tpu7x_instance_type_mapping():
    assert gcp_catalog._ACC_INSTANCE_TYPE_DICTS['tpu7x'] == {  # pylint: disable=protected-access
        4: ['tpu7x-standard-4t'],
    }
    assert gcp_catalog.get_accelerators_from_instance_type(
        'tpu7x-standard-4t') == {
            'tpu7x': 4
        }


@pytest.mark.parametrize('acc,expected', [
    ('tpu7x', True),
    ('TPU7X', True),
    ('tpu-v6e-4', False),
    ('tpu-v6e-slice', False),
    ('H100', False),
    (None, False),
])
def test_is_compute_engine_tpu(acc, expected):
    assert gcp_utils.is_compute_engine_tpu(acc) is expected


def test_canonicalize_tpu7x_name():
    assert accelerator_registry.canonicalize_accelerator_name('TPU7X',
                                                              None) == 'tpu7x'


@pytest.mark.parametrize('tpu_on_gke', [True, False])
def test_tpu7x_without_infra_does_not_pin_cloud(tpu_on_gke):
    # Without an infra, the cloud must be left to the optimizer, so that
    # GCP (and Kubernetes, when tpu7x is a known GKE accelerator) are both
    # candidates. The result must not depend on whether Kubernetes treats
    # tpu7x as a GKE TPU.
    with patch('sky.provision.kubernetes.utils.is_tpu_on_gke',
               return_value=tpu_on_gke):
        r = resources_lib.Resources(accelerators='tpu7x:4', use_spot=True)
    assert r.cloud is None
    assert r.accelerators == {'tpu7x': 4}
    # No Cloud TPU API settings (e.g. runtime_version) are added.
    assert not (r.accelerator_args or {}).get('runtime_version')


def test_tpu_v6e_without_infra_still_pins_gcp():
    with patch('sky.provision.kubernetes.utils.is_tpu_on_gke',
               return_value=False):
        r = resources_lib.Resources(accelerators='tpu-v6e-4')
    assert isinstance(r.cloud, GCP)
    assert r.accelerator_args['runtime_version'] == 'v2-alpha-tpuv6e'


@pytest.mark.usefixtures('tpu7x_catalog')
def test_tpu7x_on_gcp_is_not_a_tpu_api_vm():
    r = resources_lib.Resources(infra='gcp',
                                accelerators={'tpu7x': 4},
                                use_spot=True)
    assert not gcp_utils.is_tpu(r)
    assert not gcp_utils.is_tpu_vm(r)
    assert not gcp_utils.is_tpu_vm_pod(r)
    assert not (r.accelerator_args or {}).get('runtime_version')


@pytest.mark.usefixtures('tpu7x_catalog')
def test_tpu7x_feasible_resources():
    r = resources_lib.Resources(infra='gcp',
                                accelerators={'tpu7x': 4},
                                use_spot=True)
    feasible = GCP()._get_feasible_launchable_resources(r)  # pylint: disable=protected-access
    assert len(feasible.resources_list) == 1
    launchable = feasible.resources_list[0]
    assert launchable.instance_type == 'tpu7x-standard-4t'
    assert launchable.accelerators == {'tpu7x': 4}

    # Only the 4-chip single-host shape is supported.
    r = resources_lib.Resources(infra='gcp', accelerators={'tpu7x': 8})
    feasible = GCP()._get_feasible_launchable_resources(r)  # pylint: disable=protected-access
    assert not feasible.resources_list


@pytest.mark.usefixtures('tpu7x_catalog')
def test_tpu7x_zones_and_cost():
    regions = GCP.regions_with_offering('tpu7x-standard-4t', {'tpu7x': 4},
                                        use_spot=True,
                                        region=None,
                                        zone=None)
    zones = sorted(z.name for r in regions for z in r.zones)
    assert zones == ['us-central1-c', 'us-east5-a']
    # The chip SKU includes the VM cost; the machine type itself is free.
    assert GCP().instance_type_to_hourly_cost('tpu7x-standard-4t',
                                              use_spot=True,
                                              region='us-central1') == 0
    assert GCP().accelerators_to_hourly_cost(
        {'tpu7x': 4}, use_spot=True,
        region='us-central1') == pytest.approx(16.4616)


@pytest.mark.usefixtures('tpu7x_catalog')
def test_tpu7x_deploy_variables():
    r = resources_lib.Resources(infra='gcp/us-central1/us-central1-c',
                                instance_type='tpu7x-standard-4t',
                                accelerators={'tpu7x': 4},
                                use_spot=True)
    with patch.object(GCP, 'get_project_id', return_value='test-project'):
        deploy_vars = GCP().make_deploy_resources_variables(
            r,
            resources_utils.ClusterName('test', 'test'),
            Region('us-central1'), [Zone('us-central1-c')],
            num_nodes=1)
    assert deploy_vars['compute_engine_tpu'] is True
    # The TPU chips come with the machine type, not as guestAccelerators.
    assert deploy_vars['gpu'] is None
    assert deploy_vars['tpu_vm'] is False
    assert deploy_vars['tpu_node_name'] is None
    assert 'runtime_version' not in deploy_vars
    assert deploy_vars['image_id'] == (
        'projects/ubuntu-os-accelerator-images/global/images/family/'
        'ubuntu-accel-2404-amd64-tpu-tpu7x')
    assert deploy_vars['disk_tier'] == 'hyperdisk-balanced'
    assert deploy_vars['docker_run_options'] == ['--privileged']


def test_tpu7x_data_disks_use_hyperdisk():
    tier2name = gcp_volume_utils.get_data_disk_tier_mapping('tpu7x-standard-4t')
    for tier in resources_utils.DiskTier:
        if tier == resources_utils.DiskTier.BEST:
            continue
        assert tier2name[tier] == 'hyperdisk-balanced'


@pytest.mark.parametrize('machine_type,expected', [
    ('tpu7x-standard-4t', False),
    ('zones/us-central1-c/machineTypes/tpu7x-standard-4t', False),
    ('n2-standard-8', True),
    ('a4-highgpu-8g', True),
])
def test_bulk_insert_not_used_for_tpu_machine_types(machine_type, expected):
    # bulkInsert rejects TPU machine types, so instances.insert is used.
    config = {'machineType': machine_type}
    assert instance_utils.GCPComputeInstance._use_bulk_insert(  # pylint: disable=protected-access
        config) is expected
