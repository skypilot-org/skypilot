"""Tests for Hyperbolic cloud provider."""
import json
import os
from pathlib import Path
import tempfile

import pytest

from sky import clouds
from sky.catalog.data_fetchers import fetch_hyperbolic
from sky.clouds import hyperbolic
from sky.provision.hyperbolic import instance as hyperbolic_instance
from sky.provision.hyperbolic import utils as hyperbolic_utils
from sky.resources import Resources
from sky.utils import status_lib


def test_hyperbolic_cloud():
    """Test basic Hyperbolic cloud functionality."""
    cloud = hyperbolic.Hyperbolic()
    assert cloud.name == 'hyperbolic'
    assert cloud._REPR == 'Hyperbolic'
    assert cloud._MAX_CLUSTER_NAME_LEN_LIMIT == 120


def test_hyperbolic_credential_file_mounts():
    """Test credential file mounts for Hyperbolic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        api_key_path = Path(tmpdir) / 'api_key'
        api_key_path.touch()
        with pytest.MonkeyPatch.context() as m:
            m.setattr(hyperbolic.Hyperbolic, 'API_KEY_PATH', str(api_key_path))
            cloud = hyperbolic.Hyperbolic()
            mounts = cloud.get_credential_file_mounts()
            assert str(api_key_path) in mounts
            assert mounts[str(api_key_path)] == '~/.hyperbolic/api_key'


def test_hyperbolic_unsupported_features():
    cloud = hyperbolic.Hyperbolic()
    for feature in clouds.CloudImplementationFeatures:
        if feature in cloud._CLOUD_UNSUPPORTED_FEATURES:
            assert isinstance(cloud._CLOUD_UNSUPPORTED_FEATURES[feature], str)
        else:
            assert feature not in cloud._CLOUD_UNSUPPORTED_FEATURES


def test_hyperbolic_region_zone_validation():
    cloud = hyperbolic.Hyperbolic()
    catalog_region = cloud.regions()[0].name
    region, zone = cloud.validate_region_zone(catalog_region, None)
    assert region == catalog_region
    assert zone is None
    with pytest.raises(ValueError, match='does not support zones'):
        cloud.validate_region_zone(catalog_region, 'zone-1')


# Additional tests for error handling and wrapper functions


def test_hyperbolic_check_credentials_missing(monkeypatch, tmp_path):
    cloud = hyperbolic.Hyperbolic()
    fake_path = tmp_path / 'api_key'
    monkeypatch.setattr(os.path, 'expanduser', lambda x: str(fake_path))
    valid, msg = cloud._check_credentials()
    assert not valid
    assert 'API key not found' in msg


def test_hyperbolic_check_credentials_present(monkeypatch, tmp_path):
    """Test credential check when API key is present."""
    cloud = hyperbolic.Hyperbolic()
    api_key_path = tmp_path / 'api_key'
    api_key_path.write_text('test-key')

    # Monkeypatch both the API_KEY_PATH and os.path.expanduser
    monkeypatch.setattr(hyperbolic.Hyperbolic, 'API_KEY_PATH',
                        str(api_key_path))
    monkeypatch.setattr(os.path, 'expanduser', lambda x: str(api_key_path))

    valid, msg = cloud._check_credentials()
    assert valid
    assert msg is None


def test_hyperbolic_catalog_rows_from_rental_options():
    """Catalog rows use real regions and keep the cheapest offering."""
    vm = {
        'enabled': True,
        'machineType': 'virtual-machine',
        'gpuType': 'h100',
        'gpuCount': 1,
        'nodes': [{
            'vcpuCount': 20,
            'ramGb': 240,
            'storageGb': 720
        }],
    }
    options = [
        dict(vm, region='us-east-1', costPerHourCents=399),
        dict(vm, region='us-east-1', costPerHourCents=349),
        dict(vm, region='ca-east-3', costPerHourCents=485, enabled=False),
        dict(vm,
             region='eu-north-9',
             costPerHourCents=100,
             machineType='bare-metal'),
        dict(vm,
             region='us-east-2',
             costPerHourCents=900,
             gpuType='rtx-6000-pro',
             gpuCount=2),
    ]
    rows = fetch_hyperbolic.options_to_rows(options)
    assert [(r['InstanceType'], r['Region'], r['Price']) for r in rows] == [
        ('1x-H100-20-240', 'us-east-1', 3.49),
        ('2x-RTXPRO6000-20-240', 'us-east-2', 9.0),
    ]


@pytest.mark.parametrize('instance_type,expected', [
    ('1x-H100-75-722', ('h100', 1)),
    ('8x-RTXPRO6000-160-1536', ('rtx-6000-pro', 8)),
])
def test_hyperbolic_parse_instance_type(instance_type, expected):
    assert hyperbolic_instance._parse_instance_type(instance_type) == expected


def test_hyperbolic_query_instances_matches_exact_label(monkeypatch):
    """Rentals are matched to clusters by exact label across pages."""
    pages = {
        1: [{
            'rentalId': 'a',
            'label': 'mycluster-2',
            'status': 'Running'
        }, {
            'rentalId': 'b',
            'label': 'mycluster',
            'status': 'Pending'
        }],
        2: [{
            'rentalId': 'c',
            'label': 'mycluster',
            'status': 'Terminating'
        }],
    }

    def fake_request(self, method, endpoint, payload=None, params=None):
        del self, payload
        assert (method, endpoint) == ('GET', '/v2/on-demand/rentals/active')
        return {'items': pages[params['page']], 'totalPages': 2}

    monkeypatch.setattr(hyperbolic_utils.HyperbolicClient, '__init__',
                        lambda self: None)
    monkeypatch.setattr(hyperbolic_utils.HyperbolicClient, '_make_request',
                        fake_request)
    monkeypatch.setattr(hyperbolic_utils, '_client', None)

    assert hyperbolic_instance.query_instances('mycluster', 'mycluster') == {
        'b': (status_lib.ClusterStatus.INIT, None)
    }
    assert hyperbolic_instance.query_instances(
        'mycluster', 'mycluster', non_terminated_only=False) == {
            'b': (status_lib.ClusterStatus.INIT, None),
            'c': (None, None),
        }


def test_hyperbolic_request_retries_rate_limit(monkeypatch):
    """A 429 is retried; other errors are raised without retrying."""
    responses = []

    class FakeResponse:

        def __init__(self, status_code, body):
            self.status_code = status_code
            self.ok = status_code < 400
            self.text = json.dumps(body) if body is not None else ''
            self.content = self.text.encode()

        def json(self):
            return json.loads(self.text)

    def fake_request(*args, **kwargs):
        del args, kwargs
        return responses.pop(0)

    monkeypatch.setattr(hyperbolic_utils.requests, 'request', fake_request)
    monkeypatch.setattr(hyperbolic_utils.time, 'sleep', lambda _: None)
    monkeypatch.setattr(hyperbolic_utils.HyperbolicClient, '__init__',
                        lambda self: setattr(self, 'headers', {}))
    client = hyperbolic_utils.HyperbolicClient()

    responses[:] = [FakeResponse(429, None)] * 3 + [FakeResponse(200, [1])]
    assert client._make_request('GET', '/v2/ssh-keys') == [1]
    assert not responses

    responses[:] = [
        FakeResponse(400, {
            'message': 'Invalid input data',
            'issues': [{
                'message': 'no capacity'
            }]
        }),
        FakeResponse(200, [1]),
    ]
    with pytest.raises(hyperbolic_utils.HyperbolicError,
                       match='400: Invalid input data \\(no capacity\\)'):
        client._make_request('POST', '/v2/on-demand/rentals', payload={})
    assert len(responses) == 1
