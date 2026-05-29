import pathlib
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky import logs
from sky import resources
from sky import skypilot_config
from sky.backends import backend_utils
from sky.clouds import Region
from sky.clouds import Zone
from sky.clouds.gcp import GCP
from sky.clouds.utils import gcp_utils
from sky.provision import common
from sky.provision.gcp import config as gcp_config
from sky.provision.gcp import constants as gcp_constants
from sky.utils import common_utils
from sky.utils import config_utils


@pytest.mark.parametrize((
    'mock_return', 'expected'
), [([
    gcp_utils.GCPReservation(
        self_link=
        'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>',
        specific_reservation=gcp_utils.SpecificReservation(count=1,
                                                           in_use_count=0),
        specific_reservation_required=True,
        zone='zone')
], {
    'projects/<project>/reservations/<reservation>': 1
}),
    ([
        gcp_utils.GCPReservation(
            self_link=
            'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>',
            specific_reservation=gcp_utils.SpecificReservation(count=2,
                                                               in_use_count=1),
            specific_reservation_required=False,
            zone='zone')
    ], {
        'projects/<project>/reservations/<reservation>': 1
    }),
    ([
        gcp_utils.GCPReservation(
            self_link=
            'https://www.googleapis.com/compute/v1/projects/<project2>/zones/<zone>/reservations/<reservation>',
            specific_reservation=gcp_utils.SpecificReservation(count=1,
                                                               in_use_count=0),
            specific_reservation_required=True,
            zone='zone')
    ], {})])
def test_gcp_get_reservations_available_resources(mock_return, expected):
    gcp = GCP()
    with patch.object(gcp_utils,
                      'list_reservations_for_instance_type_in_zone',
                      return_value=mock_return):
        reservations = gcp.get_reservations_available_resources(
            'instance_type', 'region', 'zone',
            {'projects/<project>/reservations/<reservation>'})
        assert reservations == expected


def test_gcp_reservation_from_dict():
    r = gcp_utils.GCPReservation.from_dict({
        'selfLink': 'test',
        'specificReservation': {
            'count': '1',
            'inUseCount': '0'
        },
        'specificReservationRequired': True,
        'zone': 'zone'
    })

    assert r.self_link == 'test'
    assert r.specific_reservation.count == 1
    assert r.specific_reservation.in_use_count == 0
    assert r.specific_reservation_required == True
    assert r.zone == 'zone'


@pytest.mark.parametrize(('count', 'in_use_count', 'expected'), [(1, 0, 1),
                                                                 (1, 1, 0)])
def test_gcp_reservation_available_resources(count, in_use_count, expected):
    r = gcp_utils.GCPReservation(
        self_link='test',
        specific_reservation=gcp_utils.SpecificReservation(
            count=count, in_use_count=in_use_count),
        specific_reservation_required=True,
        zone='zone')

    assert r.available_resources == expected


def test_gcp_reservation_name():
    r = gcp_utils.GCPReservation(
        self_link=
        'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation-name>',
        specific_reservation=gcp_utils.SpecificReservation(count=1,
                                                           in_use_count=1),
        specific_reservation_required=True,
        zone='zone')
    assert r.name == 'projects/<project>/reservations/<reservation-name>'


@pytest.mark.parametrize(
    ('specific_reservations', 'specific_reservation_required', 'expected'), [
        ([], False, True),
        ([], True, False),
        (['projects/<project>/reservations/<reservation>'], True, True),
        (['projects/<project>/reservations/<invalid>'], True, False),
    ])
def test_gcp_reservation_is_consumable(specific_reservations,
                                       specific_reservation_required, expected):
    r = gcp_utils.GCPReservation(
        self_link=
        'https://www.googleapis.com/compute/v1/projects/<project>/zones/<zone>/reservations/<reservation>',
        specific_reservation=gcp_utils.SpecificReservation(count=1,
                                                           in_use_count=1),
        specific_reservation_required=specific_reservation_required,
        zone='zone')
    assert r.is_consumable(
        specific_reservations=specific_reservations) is expected


def test_gcp_get_user_identities_workspace_cache_bypass():
    """Test that get_user_identities bypasses cache when workspace changes."""
    # Mock the external dependencies
    with patch('sky.clouds.gcp._run_output') as mock_run_output, \
         patch.object(GCP, 'get_project_id') as mock_get_project_id, \
         patch.object(skypilot_config, 'get_workspace_cloud') as mock_get_workspace_cloud:

        # Set up different project IDs for different workspaces
        def workspace_cloud_side_effect(cloud_name):
            current_workspace = skypilot_config.get_active_workspace()
            if current_workspace == 'default':
                return {'project_id': 'default-project'}
            elif current_workspace == 'other':
                return {'project_id': 'other-project'}
            return {}

        def project_id_side_effect():
            current_workspace = skypilot_config.get_active_workspace()
            if current_workspace == 'default':
                return 'default-project'
            elif current_workspace == 'other':
                return 'other-project'
            return 'fallback-project'

        mock_get_workspace_cloud.side_effect = workspace_cloud_side_effect
        mock_get_project_id.side_effect = project_id_side_effect
        mock_run_output.return_value = 'test@example.com'

        # First call in default workspace
        result1 = GCP.get_user_identities()
        expected1 = [['test@example.com [project_id=default-project]']]
        assert result1 == expected1

        # Switch to another workspace and call again
        with skypilot_config.local_active_workspace_ctx('other'):
            result2 = GCP.get_user_identities()
            expected2 = [['test@example.com [project_id=other-project]']]
            assert result2 == expected2

        # Back to default workspace - should get the original result
        result3 = GCP.get_user_identities()
        assert result3 == expected1

        # Verify that the underlying method was called for each different workspace config
        # Should be called 3 times total: once for default, once for other, once for default again
        assert mock_run_output.call_count == 2
        assert mock_get_project_id.call_count == 2

        # Verify workspace cloud was queried for each call
        assert mock_get_workspace_cloud.call_count == 3
        mock_get_workspace_cloud.assert_any_call('gcp')


def _make_subnet(name: str, vpc_name: str, project_id: str = 'test-project'):
    return {
        'name': name,
        'network': f'projects/{project_id}/global/networks/{vpc_name}',
        'selfLink': f'https://example.com/{name}',
    }


def _make_provision_config(provider_config):
    return common.ProvisionConfig(
        provider_config=provider_config,
        authentication_config={},
        docker_config={},
        node_config={},
        count=1,
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )


def test_gcp_get_usable_vpc_and_subnet_uses_specified_subnet(monkeypatch):
    provider_config = {
        'project_id': 'test-project',
        'vpc_name': 'train-vpc',
        'subnet_names': ['train-subnet-b', 'train-subnet-a'],
    }
    provision_config = _make_provision_config(provider_config)
    monkeypatch.setattr(gcp_config, '_list_vpcnets', lambda *args, **kwargs: [{
        'name': 'train-vpc'
    }])
    monkeypatch.setattr(
        gcp_config, '_list_subnets', lambda *args, **kwargs: [
            _make_subnet('train-subnet-a', 'train-vpc'),
            _make_subnet('train-subnet-b', 'train-vpc'),
        ])

    vpc_name, subnet = gcp_config.get_usable_vpc_and_subnet(
        'cluster', 'us-central1', provision_config, MagicMock())

    assert vpc_name == 'train-vpc'
    assert subnet['name'] == 'train-subnet-b'


def test_gcp_get_usable_vpc_and_subnet_infers_vpc_from_subnet(monkeypatch):
    provider_config = {
        'project_id': 'test-project',
        'subnet_names': 'train-subnet',
    }
    provision_config = _make_provision_config(provider_config)
    monkeypatch.setattr(
        gcp_config, '_list_subnets', lambda *args, **kwargs: [
            _make_subnet('train-subnet', 'train-vpc'),
        ])

    vpc_name, subnet = gcp_config.get_usable_vpc_and_subnet(
        'cluster', 'us-central1', provision_config, MagicMock())

    assert vpc_name == 'train-vpc'
    assert subnet['name'] == 'train-subnet'


def test_gcp_get_usable_vpc_and_subnet_rejects_multiple_vpcs(monkeypatch):
    provider_config = {
        'project_id': 'test-project',
        'subnet_names': ['train-subnet-a', 'train-subnet-b'],
    }
    provision_config = _make_provision_config(provider_config)
    monkeypatch.setattr(
        gcp_config, '_list_subnets', lambda *args, **kwargs: [
            _make_subnet('train-subnet-a', 'train-vpc-a'),
            _make_subnet('train-subnet-b', 'train-vpc-b'),
        ])

    with pytest.raises(RuntimeError) as exc_info:
        gcp_config.get_usable_vpc_and_subnet('cluster', 'us-central1',
                                             provision_config, MagicMock())

    assert 'multiple VPCs' in str(exc_info.value)


def test_gcp_get_usable_vpc_and_subnet_partial_name_match(monkeypatch):
    provider_config = {
        'project_id': 'test-project',
        'vpc_name': 'train-vpc',
        'subnet_names': ['missing-subnet', 'train-subnet-b'],
    }
    provision_config = _make_provision_config(provider_config)
    monkeypatch.setattr(gcp_config, '_list_vpcnets', lambda *args, **kwargs: [{
        'name': 'train-vpc'
    }])
    monkeypatch.setattr(
        gcp_config, '_list_subnets', lambda *args, **kwargs: [
            _make_subnet('train-subnet-a', 'train-vpc'),
            _make_subnet('train-subnet-b', 'train-vpc'),
        ])

    vpc_name, subnet = gcp_config.get_usable_vpc_and_subnet(
        'cluster', 'us-central1', provision_config, MagicMock())

    assert vpc_name == 'train-vpc'
    assert subnet['name'] == 'train-subnet-b'


def test_gcp_get_usable_vpc_and_subnet_empty_subnet_names(monkeypatch):
    provider_config = {
        'project_id': 'test-project',
        'vpc_name': 'train-vpc',
        'subnet_names': [],
    }
    provision_config = _make_provision_config(provider_config)
    monkeypatch.setattr(gcp_config, '_list_vpcnets', lambda *args, **kwargs: [{
        'name': 'train-vpc'
    }])
    monkeypatch.setattr(
        gcp_config, '_list_subnets', lambda *args, **kwargs: [
            _make_subnet('train-subnet-a', 'train-vpc'),
            _make_subnet('train-subnet-b', 'train-vpc'),
        ])

    vpc_name, subnet = gcp_config.get_usable_vpc_and_subnet(
        'cluster', 'us-central1', provision_config, MagicMock())

    assert vpc_name == 'train-vpc'
    assert subnet['name'] == 'train-subnet-a'


def test_gcp_get_usable_vpc_and_subnet_shared_vpc_with_subnet_names(
        monkeypatch):
    provider_config = {
        'project_id': 'service-project',
        'vpc_name': 'host-project/train-vpc',
        'subnet_names': ['train-subnet-b'],
    }
    provision_config = _make_provision_config(provider_config)
    seen_projects = []

    def list_vpcnets(project_id, *args, **kwargs):
        seen_projects.append(project_id)
        return [{'name': 'train-vpc'}]

    def list_subnets(project_id, *args, **kwargs):
        seen_projects.append(project_id)
        return [
            _make_subnet('train-subnet-a',
                         'train-vpc',
                         project_id='host-project'),
            _make_subnet('train-subnet-b',
                         'train-vpc',
                         project_id='host-project'),
        ]

    monkeypatch.setattr(gcp_config, '_list_vpcnets', list_vpcnets)
    monkeypatch.setattr(gcp_config, '_list_subnets', list_subnets)

    vpc_name, subnet = gcp_config.get_usable_vpc_and_subnet(
        'cluster', 'us-central1', provision_config, MagicMock())

    assert seen_projects == ['host-project', 'host-project']
    assert vpc_name == 'train-vpc'
    assert subnet['name'] == 'train-subnet-b'
    assert subnet['network'] == (
        'projects/host-project/global/networks/train-vpc')


def test_gcp_minimal_compute_permissions_skip_firewall_for_custom_subnet():

    def get_effective_region_config_side_effect(cloud,
                                                region,
                                                keys,
                                                default_value=None,
                                                **kwargs):
        del cloud, region, kwargs
        if keys == ('subnet_names',):
            return ['train-subnet']
        return default_value

    with patch.object(skypilot_config,
                      'get_effective_region_config',
                      side_effect=get_effective_region_config_side_effect):
        permissions = gcp_utils.get_minimal_compute_permissions()

    for permission in gcp_constants.FIREWALL_PERMISSIONS:
        assert permission not in permissions


def test_gcp_minimal_compute_permissions_include_firewall_for_empty_subnets():

    def get_effective_region_config_side_effect(cloud,
                                                region,
                                                keys,
                                                default_value=None,
                                                **kwargs):
        del cloud, region, kwargs
        if keys == ('subnet_names',):
            return []
        return default_value

    with patch.object(skypilot_config,
                      'get_effective_region_config',
                      side_effect=get_effective_region_config_side_effect):
        permissions = gcp_utils.get_minimal_compute_permissions()

    for permission in gcp_constants.FIREWALL_PERMISSIONS:
        assert permission in permissions


def test_gcp_network_config_override_in_cluster_config(monkeypatch):
    """Test that GCP network overrides are passed through to the template."""
    monkeypatch.setattr(common_utils, 'make_cluster_name_on_cloud',
                        lambda *args, **kwargs: args[0])
    monkeypatch.setattr(backend_utils, '_get_yaml_path_from_cluster_name',
                        lambda *args, **kwargs: '/tmp/fake-gcp-yaml-path')
    monkeypatch.setattr(resources.Resources, 'make_deploy_variables',
                        lambda *args, **kwargs: {'region': 'us-central1'})
    monkeypatch.setattr(logs, 'get_logging_agent', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        backend_utils.auth_utils, 'get_or_generate_keys',
        lambda *args, **kwargs:
        ('/tmp/fake-private-key', '/tmp/fake-public-key'))

    config_dict = config_utils.Config.from_dict({})
    monkeypatch.setattr(skypilot_config, '_get_loaded_config',
                        lambda *args, **kwargs: config_dict)

    override_configs = {
        'gcp': {
            'vpc_name': 'override-vpc',
            'subnet_names': ['override-subnet'],
        },
    }

    def fill_template_side_effect(*args, **kwargs):
        del kwargs
        template_vars = args[1]
        assert template_vars['vpc_name'] == 'override-vpc'
        assert template_vars['subnet_names'] == ['override-subnet']
        raise RuntimeError('fake-error')

    monkeypatch.setattr(common_utils, 'fill_template',
                        fill_template_side_effect)

    with pytest.raises(RuntimeError):
        backend_utils.write_cluster_config(
            to_provision=resources.Resources(
                cloud=GCP(),
                instance_type='n1-standard-4',
                _cluster_config_overrides=override_configs),
            num_nodes=1,
            cluster_config_template='gcp-ray.yml.j2',
            cluster_name='fake-gcp-cluster',
            local_wheel_path=pathlib.Path('fake-wheel-path'),
            wheel_hash='fake-wheel-hash',
            region=Region(name='us-central1'),
            zones=[Zone(name='us-central1-a')])
