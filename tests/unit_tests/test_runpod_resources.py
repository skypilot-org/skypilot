"""RunPod selection must enforce the requested host resources."""
# The test exercises the provider's internal selection entry point.
# pylint: disable=protected-access
import importlib

import pandas as pd
import pytest
import requests

from sky import clouds
from sky import resources as resources_lib
from sky.catalog import common


@pytest.fixture
def offline_catalog(monkeypatch):
    """Exercise the provider and real catalog filters without catalog I/O."""
    rows = [
        ('small', 'H200-SXM', 4, 48, 256, 1),
        ('large', 'H200-SXM', 4, 48, 752, 20),
        ('unknown', 'H200-SXM', 4, 48, None, 2),
        ('eight', 'H200-SXM', 8, 128, 1600, 30),
        ('other', 'H100-SXM', 4, 48, 1000, 1),
    ]
    frame = pd.DataFrame(rows,
                         columns=[
                             'InstanceType', 'AcceleratorName',
                             'AcceleratorCount', 'vCPUs', 'MemoryGiB', 'Price'
                         ])
    frame['SpotPrice'] = frame['Price'] / 2

    def no_network(*_args, **_kwargs):
        pytest.fail('Offline RunPod resource test attempted a network request')

    monkeypatch.setattr(requests.sessions.Session, 'request', no_network)
    monkeypatch.setattr(common, 'read_catalog', lambda *args, **kwargs: frame)
    module = importlib.import_module('sky.catalog.runpod_catalog')
    monkeypatch.setattr(module, '_df', frame)


@pytest.mark.usefixtures('offline_catalog')
@pytest.mark.parametrize('memory,expected', [
    (None, ['small', 'unknown', 'large']),
    ('550+', ['large']),
    (str(752_000_000_000 / 1_073_741_824), ['large']),
    ('752', []),
    ('701+', []),
    ('700+', ['large']),
    ('550', []),
    ('753+', []),
    ('12x', ['large']),
    ('16x', []),
])
@pytest.mark.parametrize('use_spot', [False, True])
def test_gpu_memory_constraints(memory, expected, use_spot):
    resources = resources_lib.Resources(cloud=clouds.RunPod(),
                                        accelerators='H200-SXM:4',
                                        cpus='16+',
                                        memory=memory,
                                        use_spot=use_spot)
    feasible = clouds.RunPod()._get_feasible_launchable_resources(resources)
    assert [r.instance_type for r in feasible.resources_list] == expected
    assert not feasible.fuzzy_candidate_list


@pytest.mark.usefixtures('offline_catalog')
@pytest.mark.parametrize('options', [
    {
        'cpus': '16'
    },
    {
        'cpus': '49+'
    },
    {
        'accelerators': 'H200-SXM:2'
    },
    {
        'accelerators': 'B200:4'
    },
    {
        'max_hourly_cost': 10
    },
])
def test_no_fit_does_not_relax_other_constraints(options):
    arguments = dict(cloud=clouds.RunPod(),
                     accelerators='H200-SXM:4',
                     cpus='16+',
                     memory='550+')
    arguments.update(options)
    resources = resources_lib.Resources(**arguments)
    feasible = clouds.RunPod()._get_feasible_launchable_resources(resources)
    assert not feasible.resources_list
