"""Tests for explicit Azure subnet selection."""
# pylint: disable=protected-access
import itertools
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import jinja2
import jsonschema
import pytest
import yaml

import sky
from sky import exceptions
from sky import skypilot_config
from sky.adaptors import azure
from sky.provision.azure import config
from sky.utils import resources_utils
from sky.utils import schemas

_VNET = ('/subscriptions/sub/resourceGroups/rg/providers/'
         'Microsoft.Network/virtualNetworks/private')
_SUBNET = _VNET + '/subnets/workers'


@pytest.fixture(name='network')
def mock_network(monkeypatch):
    client = mock.Mock()
    client.virtual_networks.get.return_value = SimpleNamespace(subnets=[
        SimpleNamespace(id=_VNET + '/subnets/' + name,
                        name=name,
                        network_security_group=SimpleNamespace(id='nsg-' +
                                                               name))
        for name in ['endpoints', 'routers', 'workers']
    ])
    monkeypatch.setattr(azure, 'get_client', mock.Mock(return_value=client))
    return client


@pytest.mark.parametrize('order', list(itertools.permutations(range(3))))
def test_explicit_subnet_ignores_order(network, order):
    vnet = network.virtual_networks.get.return_value
    vnet.subnets = [vnet.subnets[i] for i in order]
    assert config._resolve_custom_vnet('private', 'sub', 'rg', 'eastus',
                                       _SUBNET) == (_SUBNET, 'nsg-workers')
    network.virtual_networks.get.assert_called_once_with('rg', 'private')
    network.virtual_networks.list_all.assert_not_called()


@pytest.mark.parametrize('selected', [_SUBNET.upper(), None])
def test_case_insensitive_and_legacy_selection(network, selected):
    network.virtual_networks.get.return_value.subnets[
        2].network_security_group = None
    expected = ((_SUBNET, None) if selected else
                (_VNET + '/subnets/endpoints', 'nsg-endpoints'))
    assert config._resolve_custom_vnet('private', 'sub', 'rg', 'eastus',
                                       selected) == expected


@pytest.mark.parametrize('selected', [
    '',
    'workers',
    _SUBNET + '-missing',
    _SUBNET.replace('/sub/', '/other/'),
    _SUBNET.replace('/private/', '/other/'),
    42,
])
@pytest.mark.usefixtures('network')
def test_invalid_subnet_never_falls_back(selected):
    with mock.patch.object(config.logger, 'error') as log_error:
        with pytest.raises(exceptions.ResourcesUnavailableError,
                           match='does not identify exactly one') as exc:
            config._resolve_custom_vnet('private', 'sub', 'rg', 'eastus',
                                        selected)
    log_error.assert_called_once_with(str(exc.value))
    available = ', '.join(
        repr(name) for name in ['endpoints', 'routers', 'workers'])
    assert f'Available subnets: {available}.' in str(exc.value)


@pytest.mark.parametrize('ids', [[], [_SUBNET, _SUBNET], [None]])
def test_empty_ambiguous_or_missing_ids(network, ids):
    network.virtual_networks.get.return_value.subnets = [
        SimpleNamespace(id=subnet_id, name='workers') for subnet_id in ids
    ]
    with pytest.raises(exceptions.ResourcesUnavailableError):
        config._resolve_custom_vnet('private', 'sub', 'rg', 'eastus', _SUBNET)


@pytest.mark.usefixtures('network')
def test_subnet_requires_vnet_before_lookup():
    with mock.patch.object(config.logger, 'error') as log_error:
        with pytest.raises(exceptions.ResourcesUnavailableError,
                           match='requires vpc_name') as exc:
            config._resolve_custom_vnet(None, 'sub', 'rg', 'eastus', _SUBNET)
    log_error.assert_called_once_with(str(exc.value))
    assert config._resolve_custom_vnet(None, 'sub', 'rg', 'eastus') is None
    azure.get_client.assert_not_called()


def test_vnet_in_different_resource_group(network):
    vnet = network.virtual_networks.get.return_value
    vnet.name = 'private'
    vnet.location = 'eastus'
    network.virtual_networks.get.side_effect = azure.exceptions(
    ).ResourceNotFoundError()
    network.virtual_networks.list_all.return_value = [vnet]
    assert config._resolve_custom_vnet('private', 'sub', 'other-rg', 'eastus',
                                       _SUBNET) == (_SUBNET, 'nsg-workers')


@pytest.mark.parametrize('vpc_name', ['private', None])
def test_invalid_selection_stops_before_deployment(network, monkeypatch,
                                                   vpc_name):
    resource_client = mock.Mock()
    monkeypatch.setattr(
        azure, 'get_client',
        mock.Mock(side_effect=lambda kind, _: network
                  if kind == 'network' else resource_client))
    provider = {
        'subscription_id': 'sub',
        'resource_group': 'rg',
        'location': 'eastus',
        'use_external_resource_group': True,
        'vpc_name': vpc_name,
        'subnet_id': _SUBNET + '-missing',
    }
    with pytest.raises(exceptions.ResourcesUnavailableError):
        config.bootstrap_instances('eastus', 'test',
                                   SimpleNamespace(provider_config=provider))
    assert not resource_client.mock_calls


@pytest.mark.parametrize('selected', ['', 42])
def test_schema_rejects_invalid_subnet(selected):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({'azure': {
            'subnet_id': selected
        }}, schemas.get_config_schema())


@pytest.mark.parametrize('selected,task_override', [(None, False),
                                                    (_SUBNET, False),
                                                    (_SUBNET, True)])
@pytest.mark.parametrize('has_nsg', [False, True])
@pytest.mark.parametrize('existing_deployment', [False, True])
def test_config_through_rendering_and_bootstrap(network, monkeypatch, selected,
                                                has_nsg, existing_deployment,
                                                task_override):
    settings = {'azure': {'vpc_name': 'private', 'resource_group_vm': 'rg'}}
    if selected is not None:
        settings['azure']['subnet_id'] = selected
    jsonschema.validate(settings, schemas.get_config_schema())
    monkeypatch.setattr(sky.Azure, 'get_accelerators_from_instance_type',
                        lambda *_: None)
    monkeypatch.setattr(sky.Azure, 'get_project_id', lambda *_: 'sub')
    resources = sky.Resources(cloud=sky.Azure(),
                              instance_type='Standard_D8as_v5',
                              disk_tier=resources_utils.DiskTier.MEDIUM,
                              image_id='Canonical:ubuntu:22_04:latest')
    if task_override:
        task = sky.Task.from_yaml_config({
            'resources': resources.to_yaml_config(),
            'config': {
                'azure': {
                    'vpc_name': 'private',
                    'subnet_id': selected
                }
            },
        })
        resources, = task.resources
        resources, = sky.Resources.from_yaml_config(
            resources.copy().to_yaml_config())
        assert resources.cluster_config_overrides['azure'] == {
            'vpc_name': 'private',
            'subnet_id': selected,
        }
        settings['azure'].update(vpc_name='global-vnet',
                                 subnet_id=_SUBNET.replace(
                                     '/private/', '/global-vnet/'))
    with skypilot_config.replace_skypilot_config(
            sky.Config.from_dict(settings)):
        vpc_name = skypilot_config.get_effective_region_config(
            cloud='azure',
            region='eastus',
            keys=('vpc_name',),
            default_value=None,
            override_configs=resources.cluster_config_overrides)
        variables = resources.make_deploy_variables(
            SimpleNamespace(name_on_cloud='test'),
            sky.clouds.Region('eastus'),
            None,
            1,
            dryrun=True)
    variables.update(vpc_name=vpc_name,
                     use_internal_ips=True,
                     num_nodes=1,
                     docker_image=None,
                     labels={},
                     credentials={})
    template = (Path(sky.__file__).parent / 'templates' /
                'azure-ray.yml.j2').read_text()
    provider = yaml.safe_load(
        jinja2.Template(template).render(**variables))['provider']
    assert provider['vpc_name'] == 'private'
    if selected is None:
        assert 'subnet_id' not in provider
    else:
        assert provider['subnet_id'] == selected

    if not has_nsg:
        for subnet in network.virtual_networks.get.return_value.subnets:
            subnet.network_security_group = None
    resource_client = mock.Mock()
    outputs = {
        'nsg': {
            'value': 'created-nsg'
        },
        'msi': {
            'value': 'created-msi'
        },
    }
    deployments = resource_client.deployments
    deployment = SimpleNamespace(properties=SimpleNamespace(outputs=outputs))
    if existing_deployment:
        deployments.get.return_value = deployment
    else:
        deployments.get.side_effect = azure.exceptions().ResourceNotFoundError()
        operation = deployments.create_or_update.return_value
        operation.result.return_value = deployment
    monkeypatch.setattr(
        azure, 'get_client',
        mock.Mock(side_effect=lambda kind, _: network
                  if kind == 'network' else resource_client))
    result = config.bootstrap_instances(
        'eastus', 'test', SimpleNamespace(provider_config=provider))
    network.virtual_networks.get.assert_called_once_with('rg', 'private')
    name = 'workers' if selected else 'endpoints'
    assert result.provider_config['subnet'] == _VNET + '/subnets/' + name
    assert result.provider_config['nsg'] == ('nsg-' +
                                             name if has_nsg else 'created-nsg')
    assert result.provider_config['msi'] == 'created-msi'
    resource_client.resource_groups.create_or_update.assert_not_called()
    if existing_deployment:
        deployments.create_or_update.assert_not_called()
    else:
        deployments.create_or_update.assert_called_once()
        template = deployments.create_or_update.call_args.kwargs['parameters'][
            'properties']['template']
        assert all(resource['type'] != 'Microsoft.Network/virtualNetworks'
                   for resource in template['resources'])
        assert 'subnet' not in template['outputs']
