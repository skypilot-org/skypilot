"""Tests for the OpenStack cloud integration."""

# pylint: disable=protected-access

import pathlib
import types
from unittest import mock

import jinja2
import jsonschema
import pytest
import yaml

import sky
from sky import clouds
from sky import provision
from sky.backends import cloud_vm_ray_backend
from sky.catalog import openstack_catalog
from sky.skylet import constants
from sky.utils import registry
from sky.utils import resources_utils
from sky.utils import schemas


def test_openstack_cloud_is_registered_and_exported():
    assert hasattr(clouds, 'OpenStack')
    assert hasattr(sky, 'OpenStack')
    assert isinstance(registry.CLOUD_REGISTRY.from_str('openstack'),
                      clouds.OpenStack)


def test_openstack_uses_skypilot_provisioner_and_gates_mvp_features():
    assert (clouds.OpenStack.PROVISIONER_VERSION ==
            clouds.ProvisionerVersion.SKYPILOT)
    assert clouds.OpenStack.STATUS_VERSION == clouds.StatusVersion.SKYPILOT

    unsupported = getattr(clouds.OpenStack, '_CLOUD_UNSUPPORTED_FEATURES', {})
    expected_unsupported = {
        clouds.CloudImplementationFeatures.MULTI_NODE,
        clouds.CloudImplementationFeatures.CLONE_DISK_FROM_CLUSTER,
        clouds.CloudImplementationFeatures.DOCKER_IMAGE,
        clouds.CloudImplementationFeatures.SPOT_INSTANCE,
        clouds.CloudImplementationFeatures.CUSTOM_DISK_TIER,
        clouds.CloudImplementationFeatures.CUSTOM_NETWORK_TIER,
        clouds.CloudImplementationFeatures.OPEN_PORTS,
        clouds.CloudImplementationFeatures.STORAGE_MOUNTING,
        clouds.CloudImplementationFeatures.HOST_CONTROLLERS,
        clouds.CloudImplementationFeatures.HIGH_AVAILABILITY_CONTROLLERS,
        clouds.CloudImplementationFeatures.AUTO_TERMINATE,
        clouds.CloudImplementationFeatures.AUTOSTOP,
        clouds.CloudImplementationFeatures.AUTODOWN,
        clouds.CloudImplementationFeatures.CUSTOM_MULTI_NETWORK,
        clouds.CloudImplementationFeatures.LOCAL_DISK,
    }
    assert expected_unsupported <= set(unsupported)
    assert clouds.CloudImplementationFeatures.STOP not in unsupported
    assert clouds.CloudImplementationFeatures.IMAGE_ID not in unsupported


def test_openstack_feasibility_requires_explicit_cloud():
    cloud = clouds.OpenStack()
    try:
        feasible = cloud._get_feasible_launchable_resources(sky.Resources())
    except NotImplementedError:
        pytest.fail('OpenStack feasibility is not implemented')
    assert not feasible.resources_list


def test_openstack_cpu_feasibility_uses_flavor_catalog():
    cloud = clouds.OpenStack()
    requested = sky.Resources(cloud=cloud, cpus='2+', memory='4+')
    with mock.patch(
            'sky.clouds.openstack.openstack_catalog.get_default_instance_type',
            return_value='m1.small') as get_default, mock.patch(
                'sky.catalog.openstack_catalog.check_disk_size') as check:
        feasible = cloud._get_feasible_launchable_resources(requested)

    assert len(feasible.resources_list) == 1
    launchable = feasible.resources_list[0]
    assert isinstance(launchable.cloud, clouds.OpenStack)
    assert launchable.instance_type == 'm1.small'
    assert launchable.accelerators is None
    assert get_default.call_args.kwargs['min_disk_size'] == requested.disk_size
    check.assert_called_once_with('m1.small', requested.disk_size)


def test_openstack_rejects_gpu_feasibility():
    requested = sky.Resources(cloud=clouds.OpenStack(),
                              accelerators={'A100': 1})
    feasible = clouds.OpenStack()._get_feasible_launchable_resources(requested)
    assert not feasible.resources_list


def test_openstack_rejects_unknown_price_against_budget():
    requested = sky.Resources(cloud=clouds.OpenStack(),
                              instance_type='m1.small',
                              max_hourly_cost=10)

    feasible = clouds.OpenStack()._get_feasible_launchable_resources(requested)

    assert not feasible.resources_list
    assert 'pricing is unknown' in feasible.hint


def test_openstack_cost_is_unknown_not_billable():
    cloud = clouds.OpenStack()
    assert cloud.instance_type_to_hourly_cost('m1.small', False, None,
                                              None) == 0.0
    assert cloud.accelerators_to_hourly_cost({}, False, None, None) == 0.0
    assert cloud.get_egress_cost(100) == 0.0


def test_openstack_is_in_cloud_list_and_backend_template_map():
    assert 'openstack' in constants.ALL_CLOUDS
    assert ('openstack',) in constants.SKIPPED_CLIENT_OVERRIDE_KEYS
    assert (cloud_vm_ray_backend._get_cluster_config_template(
        clouds.OpenStack()) == 'openstack-ray.yml.j2')
    assert hasattr(provision, 'openstack')


def test_openstack_bounds_cluster_name_for_nova_metadata():
    assert clouds.OpenStack.max_cluster_name_length() == 200


@pytest.mark.parametrize('config', [{
    'cloud': 'lab',
    'network': 'tenant-net',
    'external_network': 'public',
    'ssh_user': 'ubuntu',
    'use_internal_ips': False,
}, {
    'cloud': 'lab',
    'network': 'tenant-net',
    'external_network': 'public',
    'ssh_user': 'ubuntu',
}, {
    'cloud': 'lab',
    'network': 'tenant-net',
    'ssh_user': 'ubuntu',
    'use_internal_ips': True,
    'security_group_name': 'existing-sg',
}])
def test_openstack_config_schema_accepts_valid_config(config):
    schema = schemas.get_config_schema()['properties']['openstack']
    jsonschema.validate(config, schema)


@pytest.mark.parametrize('config', [{
    'network': 'tenant-net',
    'ssh_user': 'ubuntu',
    'use_internal_ips': True,
}, {
    'cloud': 'lab',
    'network': 'tenant-net',
    'ssh_user': 'ubuntu',
    'use_internal_ips': False,
}, {
    'cloud': 'lab',
    'network': 'tenant-net',
    'ssh_user': 'ubuntu',
    'use_internal_ips': True,
    'unknown': 'value',
}, {
    'cloud': 'lab',
    'network': 'tenant-net',
    'ssh_user': 'ubuntu',
    'use_internal_ips': True,
    'security_group_name': [{
        '*': 'existing-sg'
    }],
}, {
    'cloud': '',
    'network': 'tenant-net',
    'ssh_user': 'ubuntu',
    'use_internal_ips': True,
}, {
    'cloud': 'lab',
    'network': '',
    'ssh_user': 'ubuntu',
    'use_internal_ips': True,
}, {
    'cloud': 'lab',
    'network': 'tenant-net',
    'ssh_user': '',
    'use_internal_ips': True,
}])
def test_openstack_config_schema_rejects_invalid_config(config):
    schema = schemas.get_config_schema()['properties']['openstack']
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(config, schema)


def test_openstack_rejects_skypilot_image_tags():
    assert not clouds.OpenStack.is_image_tag_valid('skypilot:ubuntu-2204',
                                                   'RegionOne')


def _mock_openstack_config(config):

    def _get_nested(*, keys, default_value, **_kwargs):
        assert keys[0] == 'openstack'
        return config.get(keys[1], default_value)

    return mock.patch('sky.skypilot_config.get_nested', side_effect=_get_nested)


def test_openstack_deploy_variables_pass_provider_configuration():
    config = {
        'cloud': 'lab',
        'network': 'tenant-net',
        'external_network': 'public',
        'ssh_user': 'ubuntu',
        'use_internal_ips': False,
        'security_group_name': 'existing-sg',
    }
    resources = sky.Resources(cloud=clouds.OpenStack(),
                              instance_type='m1.small',
                              image_id='ubuntu-22.04',
                              disk_size=80)
    region = clouds.Region('RegionOne').set_zones([clouds.Zone('nova')])
    with _mock_openstack_config(config), mock.patch(
            'sky.catalog.openstack_catalog.check_disk_size') as check:
        variables = clouds.OpenStack().make_deploy_resources_variables(
            resources,
            cluster_name=resources_utils.ClusterName('test', 'test-1234'),
            region=region,
            zones=region.zones,
            num_nodes=1)
    check.assert_called_once_with('m1.small', 80)

    assert variables == {
        'availability_zone': 'nova',
        'cloud': 'lab',
        'custom_resources': None,
        'disk_size': 80,
        'external_network': 'public',
        'image_id': 'ubuntu-22.04',
        'instance_type': 'm1.small',
        'network': 'tenant-net',
        'region': 'RegionOne',
        'security_group_name': 'existing-sg',
        'ssh_user': 'ubuntu',
        'use_internal_ips': False,
    }


@pytest.mark.parametrize(('config_override', 'image_id', 'num_nodes', 'match'),
                         [
                             ({
                                 'external_network': None
                             }, 'image', 1, 'external_network'),
                             ({}, None, 1, 'image_id'),
                             ({}, 'image', 2, 'single-node'),
                         ])
def test_openstack_deploy_variables_reject_invalid_mvp_request(
        config_override, image_id, num_nodes, match):
    config = {
        'cloud': 'lab',
        'network': 'tenant-net',
        'external_network': 'public',
        'ssh_user': 'ubuntu',
        'use_internal_ips': False,
        **config_override,
    }
    resources = sky.Resources(cloud=clouds.OpenStack(),
                              instance_type='m1.small',
                              image_id=image_id)
    with _mock_openstack_config(config), pytest.raises(ValueError, match=match):
        clouds.OpenStack().make_deploy_resources_variables(
            resources,
            cluster_name=resources_utils.ClusterName('test', 'test-1234'),
            region=clouds.Region('RegionOne'),
            zones=None,
            num_nodes=num_nodes)


def test_openstack_template_renders_external_provisioner_config():
    template_path = pathlib.Path('sky/templates/openstack-ray.yml.j2')
    assert template_path.exists()
    rendered = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(
        template_path.read_text(encoding='utf-8')).render(
            cluster_name_on_cloud='test-1234',
            num_nodes=1,
            cloud='yes',
            region='on',
            availability_zone='null',
            network='off',
            external_network='true',
            use_internal_ips=False,
            security_group_name='no',
            ssh_user='yes',
            ssh_private_key='/tmp/sky-key',
            instance_type='yes',
            image_id='null',
            disk_size=80,
            sky_ray_yaml_remote_path='~/.sky/ray.yml',
            sky_ray_yaml_local_path='/tmp/ray.yml',
            sky_remote_path='~/.sky/wheels',
            sky_wheel_hash='hash',
            sky_local_path='/tmp/sky.whl',
            credentials={},
            initial_setup_commands=['echo FIRST;', 'echo SECOND;'],
            conda_installation_commands='true',
            uv_installation_commands='true',
            ray_skypilot_installation_commands='true',
            copy_skypilot_templates_commands='true',
            ssh_max_sessions_config='true')
    config = yaml.safe_load(rendered)

    assert config['provider'] == {
        'type': 'external',
        'module': 'sky.provision.openstack',
        'cloud': 'yes',
        'region': 'on',
        'availability_zone': 'null',
        'network': 'off',
        'external_network': 'true',
        'use_internal_ips': False,
        'security_group_name': 'no',
        'cache_stopped_nodes': True,
        'disable_launch_config_check': True,
    }
    assert config['max_workers'] == 0
    assert config['auth']['ssh_user'] == 'yes'
    node_config = config['available_node_types']['ray_head_default'][
        'node_config']
    assert node_config == {
        'InstanceType': 'yes',
        'ImageId': 'null',
        'DiskSize': 80,
        'AvailabilityZone': 'null',
        'AuthorizedKey': 'skypilot:ssh_public_key_content',
    }
    assert len(config['setup_commands']) == 1
    setup_lines = [
        line.strip() for line in config['setup_commands'][0].splitlines()
    ]
    assert 'echo FIRST;' in setup_lines
    assert 'echo SECOND;' in setup_lines


def test_openstack_regions_and_zone_provision_loop_use_local_catalog():
    offered = [
        clouds.Region('RegionOne').set_zones(
            [clouds.Zone('nova'), clouds.Zone('edge')]),
        clouds.Region('RegionTwo').set_zones([clouds.Zone('nova')]),
    ]
    with mock.patch(
            'sky.clouds.openstack.catalog.get_region_zones_for_instance_type',
            return_value=offered):
        regions = clouds.OpenStack.regions_with_offering('m1.small',
                                                         None,
                                                         False,
                                                         region='RegionOne',
                                                         zone='edge')

    assert [region.name for region in regions] == ['RegionOne']
    assert [zone.name for zone in regions[0].zones] == ['edge']

    with mock.patch.object(clouds.OpenStack,
                           'regions_with_offering',
                           return_value=[offered[0]]):
        attempts = list(
            clouds.OpenStack.zones_provision_loop(region='RegionOne',
                                                  num_nodes=1,
                                                  instance_type='m1.small'))
    assert [[zone.name for zone in attempt] for attempt in attempts
           ] == [['nova'], ['edge']]


def test_openstack_catalog_metadata_methods_delegate():
    with mock.patch('sky.clouds.openstack.catalog.regions',
                    return_value=[clouds.Region('RegionOne')]) as regions, \
         mock.patch(('sky.clouds.openstack.catalog.'
                     'get_vcpus_mem_from_instance_type'),
                    return_value=(2, 4)) as vcpus_mem, \
         mock.patch('sky.clouds.openstack.catalog.instance_type_exists',
                    return_value=True) as exists, \
         mock.patch('sky.clouds.openstack.catalog.get_arch_from_instance_type',
                    return_value='x86_64') as arch, \
         mock.patch('sky.clouds.openstack.catalog.validate_region_zone',
                    return_value=('RegionOne', 'nova')) as validate:
        assert clouds.OpenStack.regions() == [clouds.Region('RegionOne')]
        assert (
            clouds.OpenStack.get_vcpus_mem_from_instance_type('m1.small') == (
                2, 4))
        assert (clouds.OpenStack.get_arch_from_instance_type('m1.small') ==
                'x86_64')
        assert clouds.OpenStack().instance_type_exists('m1.small')
        assert (clouds.OpenStack().validate_region_zone('RegionOne',
                                                        'nova') == ('RegionOne',
                                                                    'nova'))

    regions.assert_called_once_with(clouds='openstack')
    vcpus_mem.assert_called_once_with('m1.small', clouds='openstack')
    exists.assert_called_once_with('m1.small', clouds='openstack')
    arch.assert_called_once_with('m1.small', clouds='openstack')
    validate.assert_called_once_with('RegionOne', 'nova', clouds='openstack')
    assert clouds.OpenStack.get_zone_shell_cmd() is None


def test_openstack_resources_serialize_without_local_catalog(
        monkeypatch, tmp_path):
    monkeypatch.setattr(openstack_catalog, '_active_context_path',
                        lambda: str(tmp_path / 'missing-context.json'))
    monkeypatch.setattr(openstack_catalog, '_active_catalog_path', None)
    monkeypatch.setattr(openstack_catalog, '_active_context_signature', None)
    monkeypatch.setattr(openstack_catalog, '_active_catalog_signature', None)
    monkeypatch.setattr(openstack_catalog, '_df', None)

    resources = sky.Resources(cloud=clouds.OpenStack(),
                              instance_type='m1.small')

    config = resources.to_yaml_config()

    assert config['instance_type'] == 'm1.small'
    assert 'memory' not in config


def test_openstack_resources_ignore_client_catalog_memory():
    with mock.patch.object(clouds.OpenStack,
                           'get_vcpus_mem_from_instance_type',
                           return_value=(64, 512)) as get_vcpus_mem:
        resources = sky.Resources(cloud=clouds.OpenStack(),
                                  instance_type='shared-flavor')

        config = resources.to_yaml_config()

    assert 'memory' not in config
    get_vcpus_mem.assert_not_called()


def test_openstack_resources_preserve_explicit_memory():
    resources = sky.Resources(cloud=clouds.OpenStack(),
                              instance_type='shared-flavor',
                              memory='8')

    config = resources.to_yaml_config()

    assert config['memory'] == '8'


def test_instance_type_inference_skips_uninitialized_openstack_catalog():
    aws_cloud = clouds.AWS()
    openstack_cloud = clouds.OpenStack()
    error = openstack_catalog.OpenStackCatalogNotInitializedError(
        'catalog is not initialized')
    with mock.patch(
            'sky.check.get_cached_enabled_clouds_or_refresh',
            return_value=[aws_cloud, openstack_cloud]), \
         mock.patch.object(aws_cloud,
                           'instance_type_exists',
                           return_value=True), \
         mock.patch.object(openstack_cloud,
                           'instance_type_exists',
                           side_effect=error):
        resources = sky.Resources(instance_type='m5.12xlarge')
        resources.validate()

    assert resources.cloud == aws_cloud


def test_instance_type_inference_propagates_other_catalog_errors():
    aws_cloud = clouds.AWS()
    openstack_cloud = clouds.OpenStack()
    with mock.patch(
            'sky.check.get_cached_enabled_clouds_or_refresh',
            return_value=[aws_cloud, openstack_cloud]), \
         mock.patch.object(aws_cloud,
                           'instance_type_exists',
                           return_value=False), \
         mock.patch.object(openstack_cloud,
                           'instance_type_exists',
                           side_effect=ValueError('malformed catalog')):
        resources = sky.Resources(instance_type='m1.small')
        with pytest.raises(ValueError, match='malformed catalog'):
            resources.validate()


def test_openstack_credentials_are_not_uploaded_to_workload_nodes():
    with _mock_openstack_config({'cloud': 'lab'}):
        assert not clouds.OpenStack().get_credential_file_mounts()


def test_openstack_get_image_size_resolves_name_or_id():
    image_service = mock.Mock()
    image_service.find_image.return_value = types.SimpleNamespace(size=5 *
                                                                  1024**3)
    connection = types.SimpleNamespace(image=image_service)
    with _mock_openstack_config({'cloud': 'lab'}), mock.patch(
            'sky.adaptors.openstack.get_connection',
            return_value=connection) as get_connection:
        assert clouds.OpenStack.get_image_size('ubuntu-22.04',
                                               'RegionOne') == 5.0

    get_connection.assert_called_once_with('lab', 'RegionOne')
    image_service.find_image.assert_called_once_with('ubuntu-22.04',
                                                     ignore_missing=True)


@pytest.mark.parametrize(('size_bytes', 'min_disk', 'expected'), [
    (5 * 1024**3 + 1, 4, 6.0),
    (5 * 1024**3, 8, 8.0),
])
def test_openstack_get_image_size_honors_bytes_and_min_disk(
        size_bytes, min_disk, expected):
    connection = types.SimpleNamespace(image=mock.Mock())
    connection.image.find_image.return_value = types.SimpleNamespace(
        size=size_bytes, min_disk=min_disk)
    with _mock_openstack_config({'cloud': 'lab'}), mock.patch(
            'sky.adaptors.openstack.get_connection', return_value=connection):
        assert clouds.OpenStack.get_image_size('image', 'RegionOne') == expected


def test_openstack_get_image_size_rejects_unknown_image():
    connection = types.SimpleNamespace(image=mock.Mock())
    connection.image.find_image.return_value = None
    with _mock_openstack_config({'cloud': 'lab'}), mock.patch(
            'sky.adaptors.openstack.get_connection',
            return_value=connection), pytest.raises(ValueError,
                                                    match='not found'):
        clouds.OpenStack.get_image_size('missing-image', 'RegionOne')


def test_openstack_compute_credential_check_refreshes_flavor_catalog():
    connection = mock.Mock()
    connection.current_project_id = 'project-id'
    connection.config = types.SimpleNamespace(region_name='RegionOne')
    with _mock_openstack_config({'cloud': 'lab'}), \
         mock.patch('sky.clouds.openstack.adaptors_common.can_import_modules',
                    return_value=True), \
         mock.patch('sky.adaptors.openstack.get_connection',
                    return_value=connection) as get_connection, \
         mock.patch('sky.catalog.openstack_catalog.refresh_catalog') as refresh:
        assert clouds.OpenStack._check_compute_credentials() == (True, None)

    connection.authorize.assert_called_once_with()
    get_connection.assert_called_once_with('lab')
    refresh.assert_called_once_with('lab',
                                    project_id='project-id',
                                    region='RegionOne',
                                    connection=connection)


def test_openstack_compute_credential_check_reports_missing_dependency():
    with mock.patch('sky.clouds.openstack.adaptors_common.can_import_modules',
                    return_value=False):
        ok, reason = clouds.OpenStack._check_compute_credentials()
    assert not ok
    assert 'skypilot[openstack]' in reason


def test_openstack_compute_credential_check_reports_api_error():
    connection = mock.Mock()
    connection.authorize.side_effect = RuntimeError('certificate verify failed')
    with _mock_openstack_config({'cloud': 'lab'}), \
         mock.patch('sky.clouds.openstack.adaptors_common.can_import_modules',
                    return_value=True), \
         mock.patch('sky.adaptors.openstack.get_connection',
                    return_value=connection), \
         mock.patch('sky.catalog.openstack_catalog.refresh_catalog') as refresh:
        ok, reason = clouds.OpenStack._check_compute_credentials()

    assert not ok
    assert 'certificate verify failed' in reason
    refresh.assert_not_called()


def test_openstack_user_identity_uses_user_and_project_ids():
    connection = mock.Mock()
    connection.current_project_id = 'project-id'
    connection.session.get_user_id.return_value = 'user-id'
    with _mock_openstack_config({'cloud': 'lab'}), mock.patch(
            'sky.adaptors.openstack.get_connection', return_value=connection):
        identities = clouds.OpenStack.get_user_identities()
    assert identities == [['user-id [project_id=project-id]']]


def test_openstack_feasibility_rejects_undersized_flavor_root_disk():
    requested = sky.Resources(cloud=clouds.OpenStack(),
                              instance_type='m1.small',
                              disk_size=100)
    with mock.patch('sky.catalog.openstack_catalog.check_disk_size',
                    side_effect=ValueError('root disk is too small')) as check:
        with pytest.raises(ValueError, match='root disk is too small'):
            clouds.OpenStack()._get_feasible_launchable_resources(requested)
    check.assert_called_once_with('m1.small', 100)


def test_openstack_deploy_rejects_undersized_flavor_root_disk():
    config = {
        'cloud': 'lab',
        'network': 'tenant-net',
        'ssh_user': 'ubuntu',
        'use_internal_ips': True,
    }
    resources = sky.Resources(cloud=clouds.OpenStack(),
                              instance_type='m1.small',
                              image_id='image',
                              disk_size=100)
    with _mock_openstack_config(config), mock.patch(
            'sky.catalog.openstack_catalog.check_disk_size',
            side_effect=ValueError('root disk is too small')) as check, \
         pytest.raises(ValueError, match='root disk is too small'):
        clouds.OpenStack().make_deploy_resources_variables(
            resources,
            cluster_name=resources_utils.ClusterName('test', 'test-1234'),
            region=clouds.Region('RegionOne'),
            zones=None,
            num_nodes=1)
    check.assert_called_once_with('m1.small', 100)
