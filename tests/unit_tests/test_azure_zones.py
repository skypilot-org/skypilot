"""Azure native zone selection, SDK deployment, and failover regressions."""
# pylint: disable=protected-access,redefined-outer-name

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

from jinja2 import Environment
import pandas as pd
import pytest
import yaml

import sky
from sky.backends import cloud_vm_ray_backend
from sky.catalog import azure_catalog
from sky.clouds import Azure
from sky.provision.azure import instance as azure_instance
from sky.utils import dag_utils


@pytest.fixture
def catalog(monkeypatch):

    rows = [{
        'InstanceType': name,
        'Region': region,
        'Price': price,
        'SpotPrice': price / 2,
        'vCPUs': 2,
        'MemoryGiB': 8,
        'AcceleratorName': None,
        'AcceleratorCount': 0
    } for name, region, price in [(
        'Standard_Test', 'eastus',
        0.1), ('Standard_Test', 'westus',
               0.2), ('Standard_Regional', 'eastus', 0.3)]]
    result = azure_catalog
    monkeypatch.setattr(result, '_df', pd.DataFrame(rows))
    monkeypatch.setattr(result.azure, 'get_subscription_id',
                        lambda: 'subscription-a')
    result._get_resource_skus.cache_clear()
    yield result
    result._get_resource_skus.cache_clear()


def _sku(name='Standard_Test',
         regions=('eastus',),
         zones=('1', '2'),
         restrictions=(),
         capabilities=()):
    return NS(
        name=name,
        resource_type='virtualMachines',
        locations=list(regions),
        location_info=[
            NS(location=region, zones=list(zones)) for region in regions
        ],
        restrictions=list(restrictions),
        capabilities=[NS(name=key, value=value) for key, value in capabilities])


def _restrict(kind, locations, zones=(), *, legacy=False):
    return NS(type=kind,
              values=list(locations) if legacy else None,
              restriction_info=NS(locations=[] if legacy else list(locations),
                                  zones=list(zones)))


def _serve(catalog, monkeypatch, skus):
    listing = Mock(return_value=skus)
    monkeypatch.setattr(catalog.azure, 'get_client',
                        lambda *_: NS(resource_skus=NS(list=listing)))
    return listing


def test_zone_restrictions_are_subscription_and_location_relative(
        catalog, monkeypatch):
    sku = _sku(regions=('eastus', 'westus'),
               restrictions=[
                   _restrict('Zone', ['eastus'], ['1']),
                   _restrict('Location', ['westus']),
               ])
    listing = _serve(catalog, monkeypatch, [sku])
    assert catalog.get_instance_type_zones('Standard_Test', 'eastus') == ['2']
    assert catalog.get_instance_type_zones('Standard_Test', 'westus') is None
    assert catalog.validate_region_zone('EASTUS', '2') == ('eastus', '2')
    with pytest.raises(ValueError, match='current subscription'):
        catalog.validate_region_zone('eastus', '1')
    with pytest.raises(ValueError, match='requires a region'):
        catalog.validate_region_zone(None, '2')
    assert listing.call_count == 2
    monkeypatch.setattr(catalog.azure, 'get_subscription_id',
                        lambda: 'subscription-b')
    catalog.get_instance_type_zones('Standard_Test', 'eastus')
    assert listing.call_count == 3
    listing.assert_called_with(filter='location eq \'eastus\'')


def test_catalog_keeps_regional_offerings_and_filters_restricted_locations(
        catalog, monkeypatch):
    _serve(catalog, monkeypatch, [
        _sku(regions=('eastus', 'westus'),
             restrictions=[_restrict('Location', ['westus'], legacy=True)]),
        _sku('Standard_Regional', zones=()),
    ])
    regions = catalog.get_region_zones_for_instance_type('Standard_Test', False)
    assert [(r.name, [z.name for z in r.zones]) for r in regions
           ] == [('eastus', ['1', '2'])]
    regional = catalog.get_region_zones_for_instance_type(
        'Standard_Regional', False)
    assert len(regional) == 1 and regional[0].zones is None
    assert catalog.get_hourly_cost('Standard_Test', region='eastus',
                                   zone='2') == 0.1
    # Billing remains available after the subscription loses an offering.
    assert catalog.get_hourly_cost('Standard_Regional',
                                   region='eastus',
                                   zone='2') == 0.3


def test_exhausted_zonal_offering_does_not_become_regional(
        catalog, monkeypatch):
    _serve(catalog, monkeypatch, [
        _sku(restrictions=[_restrict('Zone', ['eastus'], ['1', '2'])],
             capabilities=[('CpuArchitectureType', 'x64')]),
        _sku('Standard_Regional',
             zones=(),
             capabilities=[('CpuArchitectureType', 'x64')]),
    ])
    assert catalog.get_instance_type_zones('Standard_Test', 'eastus') is None
    assert catalog.get_region_zones_for_instance_type('Standard_Test',
                                                      False) == []
    assert catalog.get_cpu_instance_types('eastus') == {'Standard_Regional'}
    assert list(
        Azure.zones_provision_loop(region='eastus',
                                   num_nodes=1,
                                   instance_type='Standard_Test')) == []
    assert list(
        Azure.zones_provision_loop(region='eastus',
                                   num_nodes=1,
                                   instance_type='Standard_Regional')) == [
                                       None
                                   ]


def test_sku_api_errors_are_not_cached_or_silently_treated_as_no_zones(
        catalog, monkeypatch):
    listing = _serve(catalog, monkeypatch, [])
    listing.side_effect = [RuntimeError('denied'), [_sku()]]
    with pytest.raises(RuntimeError, match='denied'):
        catalog.get_instance_type_zones('Standard_Test', 'eastus')
    assert catalog.get_instance_type_zones('Standard_Test',
                                           'eastus') == ['1', '2']


def test_cpu_capabilities_exclude_arm_gpu_unknown_arch_and_restricted_skus(
        catalog, monkeypatch):
    _serve(catalog, monkeypatch, [
        _sku('x64',
             capabilities=[('CpuArchitectureType', 'x64'),
                           ('DiskControllerTypes', 'NVMe')]),
        _sku('arm', capabilities=[('CpuArchitectureType', 'Arm64')]),
        _sku('gpu',
             capabilities=[('CpuArchitectureType', 'x64'), ('GPUs', '1')]),
        _sku('gen1',
             capabilities=[('CpuArchitectureType', 'x64'),
                           ('HyperVGenerations', 'V1')]),
        _sku('unknown'),
        _sku('restricted', restrictions=[_restrict('Location', ['eastus'])]),
    ])
    assert catalog.get_cpu_instance_types('eastus') == {'x64', 'gen1'}
    assert catalog.get_instance_type_capabilities(
        'x64', 'eastus')['DiskControllerTypes'] == 'NVMe'


def test_regional_cost_does_not_depend_on_current_subscription_capacity(
        catalog, monkeypatch):
    listing = _serve(catalog, monkeypatch, [])
    listing.side_effect = RuntimeError('subscription unavailable')
    assert catalog.get_hourly_cost('Standard_Test', region='eastus',
                                   zone='2') == 0.1
    listing.assert_not_called()


@pytest.fixture
def cloud():

    return Azure


def test_native_provision_loop_exposes_each_available_zone(
        cloud, catalog, monkeypatch):
    _serve(catalog, monkeypatch, [_sku(zones=('2', '5'))])
    attempts = list(
        cloud.zones_provision_loop(region='eastus',
                                   num_nodes=1,
                                   instance_type='Standard_Test'))
    assert [[z.name for z in zones] for zones in attempts] == [['2'], ['5']]
    selected = cloud.regions_with_offering('Standard_Test', None, False,
                                           'eastus', '5')
    assert [z.name for z in selected[0].zones] == ['5']


def test_native_provision_loop_preserves_regional_only_skus(
        cloud, catalog, monkeypatch):
    _serve(catalog, monkeypatch, [_sku('Standard_Regional', zones=())])
    assert list(
        cloud.zones_provision_loop(region='eastus',
                                   num_nodes=1,
                                   instance_type='Standard_Regional')) == [
                                       None
                                   ]


@pytest.mark.parametrize('controllers,expected', [('NVMe', 'NVMe'),
                                                  ('SCSI, NVMe', None),
                                                  ('', None)])
def test_deployment_renders_native_zone_and_required_controller(
        cloud, catalog, monkeypatch, controllers, expected):

    _serve(catalog, monkeypatch,
           [_sku(capabilities=[('DiskControllerTypes', controllers)])])
    monkeypatch.setattr(cloud, 'get_accelerators_from_instance_type',
                        lambda *_: None)
    monkeypatch.setattr(cloud, 'get_project_id', lambda *_: 'subscription-a')
    resource = sky.Resources(infra='azure/eastus/2',
                             instance_type='Standard_Test',
                             image_id='Canonical:offer:sku:latest',
                             disk_tier='medium')
    values = cloud().make_deploy_resources_variables(
        resource, NS(name_on_cloud='test'), sky.clouds.Region('eastus'),
        [sky.clouds.Zone('2')], 1)
    assert values['zones'] == '2'
    assert values['disk_controller_type'] == expected
    source = (Path(__file__).resolve().parents[2] /
              'sky/templates/azure-ray.yml.j2').read_text()
    rendered = Environment().from_string(source).render(
        **values,
        docker_image=None,
        labels={},
        credentials={},
        num_nodes=1,
        disk_size=30,
        vpc_name=None,
        ssh_proxy_command=None,
        disk_performance_tier=None,
    )
    config = yaml.safe_load(rendered)
    assert config['provider']['availability_zone'] == '2'
    arm = config['available_node_types']['ray.head.default']['node_config'][
        'azure_arm_parameters']
    assert arm.get('diskControllerType') == expected


def test_legacy_image_catalog_without_base_column_remains_usable(
        catalog, monkeypatch):
    frame = pd.DataFrame([{
        'Tag': 'skypilot:legacy',
        'Region': None,
        'ImageId': 'Canonical:offer:gen2:latest'
    }])
    monkeypatch.setattr(catalog, '_image_df', frame)
    refresh = Mock(return_value=frame)
    monkeypatch.setattr(catalog.common, 'read_catalog', refresh)
    assert catalog.get_image_id_from_tag('skypilot:legacy',
                                         None) == 'Canonical:offer:gen2:latest'
    refresh.assert_not_called()
    assert catalog.get_image_id_from_tag(
        'skypilot:legacy', None, use_base_image=True) is None
    refresh.assert_called_once()


@pytest.mark.parametrize('error_class',
                         ['HttpResponseError', 'ServiceRequestError'])
def test_community_metadata_errors_do_not_trigger_image_fallback(
        cloud, catalog, monkeypatch, error_class):
    _serve(catalog, monkeypatch, [])
    monkeypatch.setattr(cloud, 'get_project_id', lambda *_: 'subscription-a')
    error = sky.exceptions.ResourcesUnavailableError('metadata unavailable')
    error.__cause__ = getattr(catalog.azure.exceptions(),
                              error_class)('metadata unavailable')
    metadata = Mock(side_effect=error)
    monkeypatch.setattr('sky.clouds.azure.azure_utils.get_community_image',
                        metadata)
    for _ in range(2):
        with pytest.raises(sky.exceptions.ResourcesUnavailableError,
                           match='metadata unavailable'):
            cloud._community_image_supports_nvme(
                '/CommunityGalleries/test/Images/default', 'eastus')
    assert metadata.call_count == 2


@pytest.mark.parametrize('settings,expected,metadata_calls', [
    ({}, 'Canonical:offer:gen2:latest', 1),
    ({
        'not_found': True
    }, 'Canonical:offer:gen2:latest', 1),
    ({
        'features': 'SCSI, NVMe'
    }, 'community', 1),
    ({
        'base': 'Canonical:offer:gen2:1.2.3'
    }, 'Canonical:offer:gen2:1.2.3', 1),
    ({
        'available': False
    }, 'Canonical:offer:gen2:latest', 0),
    ({
        'controllers': 'SCSI,NVMe'
    }, 'community', 0),
    ({
        'explicit': 'Custom:offer:gen2:9'
    }, 'Custom:offer:gen2:9', 0),
    ({
        'gpu': True
    }, 'community', 0),
])
def test_default_cpu_image_matches_required_controller(cloud, catalog,
                                                       monkeypatch, settings,
                                                       expected,
                                                       metadata_calls):
    _serve(catalog, monkeypatch, [
        _sku(capabilities=[('DiskControllerTypes',
                            settings.get('controllers', 'NVMe'))])
    ])
    community = '/CommunityGalleries/test/Images/default'
    monkeypatch.setattr(
        catalog, '_image_df',
        pd.DataFrame([{
            'Tag': tag,
            'Region': None,
            'ImageId': community,
            'BaseImageId': settings.get('base', 'Canonical:offer:gen2')
        } for tag in ('skypilot:custom-cpu-ubuntu-v2',
                      'skypilot:custom-gpu-ubuntu-v2')]))
    monkeypatch.setattr(catalog, 'get_gen_version_from_instance_type',
                        lambda *_: 'V2')
    monkeypatch.setattr(cloud, 'get_accelerators_from_instance_type',
                        lambda *_: {'T4': 1} if settings.get('gpu') else None)
    monkeypatch.setattr(cloud, 'get_project_id', lambda *_: 'subscription-a')
    if not settings.get('available', True):
        monkeypatch.setattr(catalog, 'COMMUNITY_IMAGE_AVAILABLE_REGIONS', set())
    features = settings.get('features')
    metadata = Mock(return_value=NS(
        features=[NS(name='DiskControllerTypes', value=features
                    )] if features else None))
    if settings.get('not_found'):
        error = sky.exceptions.ResourcesUnavailableError('image absent')
        error.__cause__ = catalog.azure.exceptions().ResourceNotFoundError(
            'image absent')
        metadata.side_effect = error
    monkeypatch.setattr('sky.clouds.azure.azure_utils.get_community_image',
                        metadata)
    resource = sky.Resources(infra='azure/eastus/2',
                             instance_type='Standard_Test',
                             image_id=settings.get('explicit'),
                             disk_tier='medium')
    for _ in range(2):
        values = cloud().make_deploy_resources_variables(
            resource, NS(name_on_cloud='test'), sky.clouds.Region('eastus'),
            [sky.clouds.Zone('2')], 1)
        if expected == 'community':
            assert values['community_gallery_image_id'] == community
        else:
            assert ':'.join(values[f'image_{key}']
                            for key in ('publisher', 'offer', 'sku',
                                        'version')) == expected
    assert metadata.call_count == metadata_calls


def test_provisioner_uses_native_zone_and_reports_actual_resume_zone(
        monkeypatch):
    module = azure_instance
    classes = {
        name: NS for name in [
            'HardwareProfile', 'NetworkProfile', 'NetworkInterfaceReference',
            'OSProfile', 'LinuxConfiguration', 'SshConfiguration',
            'SshPublicKey', 'ImageReference', 'StorageProfile', 'OSDisk',
            'ManagedDiskParameters', 'VirtualMachine', 'VirtualMachineIdentity'
        ]
    }
    classes.update(DiskCreateOptionTypes=NS(FROM_IMAGE='FromImage'),
                   DiskDeleteOptionTypes=NS(DELETE='Delete'))
    monkeypatch.setattr(module.azure, 'azure_mgmt_models',
                        lambda *_: NS(**classes))
    create = Mock(return_value=NS(result=lambda: NS(name='helper')))
    node = {
        'azure_arm_parameters': {
            'vmSize': 'Standard_Test',
            'publicKey': 'public',
            'adminUsername': 'sky',
            'cloudInitSetupCommands': '',
            'imagePublisher': 'Canonical',
            'imageOffer': 'offer',
            'imageSku': 'sku',
            'imageVersion': 'latest',
            'osDiskTier': 'Standard_LRS',
            'osDiskSizeGB': 30,
            'diskControllerType': 'NVMe'
        }
    }
    module._create_vm(NS(virtual_machines=NS(begin_create_or_update=create)),
                      'helper', {}, {
                          'location': 'eastus',
                          'availability_zone': '2',
                          'msi': 'msi',
                          'resource_group': 'rg'
                      }, node, 'nic')
    vm = create.call_args.kwargs['parameters']
    assert vm.zones == ['2']
    assert vm.storage_profile.disk_controller_type == 'NVMe'
    assert module._get_cluster_zone([NS(name='head', zones=['3'])],
                                    'head') == '3'
    assert module._get_cluster_zone([NS(name='head', zones=None)],
                                    'head') is None


def test_native_failover_blocks_only_the_failed_zone():
    module = cloud_vm_ray_backend

    resources = sky.Resources(infra='azure/eastus',
                              instance_type='Standard_Test')
    blocked = set()
    module.FailoverCloudErrorHandlerV2._azure_handler(
        blocked, resources, sky.clouds.Region('eastus'), [sky.clouds.Zone('2')],
        RuntimeError('capacity unavailable'))
    assert len(blocked) == 1
    assert next(iter(blocked)).zone == '2'


@pytest.mark.parametrize('actual', ['3', None])
def test_native_provision_record_reports_actual_existing_vm_zone(
        monkeypatch, actual):
    module = azure_instance
    vm = NS(name='head',
            tags=module.constants.HEAD_NODE_TAGS,
            zones=[actual] if actual else None)
    monkeypatch.setattr(module.azure, 'get_client', lambda *_: NS())
    monkeypatch.setattr(module, '_get_instance_status',
                        lambda *_: module.AzureInstanceStatus.RUNNING)

    def instances(*_args, **kwargs):
        states = kwargs.get('status_filters', [])
        return [vm] if module.AzureInstanceStatus.RUNNING in states else []

    monkeypatch.setattr(module, '_filter_instances', instances)
    config = NS(provider_config={
        'resource_group': 'rg',
        'subscription_id': 'sub',
        'availability_zone': '2'
    },
                tags={},
                count=1,
                resume_stopped_nodes=True)
    record = module.run_instances('eastus', 'cluster', 'cloud-cluster', config)
    assert record.zone == actual
    assert record.head_instance_id == 'head'


def test_sdk_serializes_azure_zone_for_server_validation():

    task = sky.Task(run='true').set_resources(
        sky.Resources(infra='azure/eastus/2', cpus=2))
    dag = sky.Dag()
    dag.add(task)
    serialized = dag_utils.dump_dag_to_yaml_str(dag)
    assert 'azure/eastus/2' in serialized


def test_accelerator_zone_filter_handles_no_matching_offerings(
        catalog, monkeypatch):
    _serve(catalog, monkeypatch, [_sku(), _sku('Standard_Regional', zones=())])
    monkeypatch.setattr(
        catalog, '_df',
        catalog._df[catalog._df['InstanceType'] == 'Standard_Regional'])
    assert catalog.get_instance_type_for_accelerator('T4',
                                                     1,
                                                     region='eastus',
                                                     zone='2') == (None, [])
