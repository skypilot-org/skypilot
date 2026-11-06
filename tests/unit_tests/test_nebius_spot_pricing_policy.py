"""Tests for capping Nebius spot prices with a spot pricing policy."""
# pylint: disable=protected-access,redefined-outer-name
from unittest import mock

import jsonschema
import pytest
import yaml

from sky import resources as resources_lib
from sky import skypilot_config
from sky.provision import common as provision_common
from sky.provision.nebius import constants as nebius_constants
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import resources_utils
from sky.utils import schemas

pytest.importorskip('nebius')

# pylint: disable=wrong-import-position
from sky.adaptors import nebius
from sky.clouds import nebius as nebius_cloud
from sky.provision.nebius import instance as nebius_instance
from sky.provision.nebius import utils

_PLATFORM = 'gpu-h100-sxm'
_PRESET = '1gpu-16vcpu-200gb'
_INSTANCE_TYPE = f'{_PLATFORM}_{_PRESET}'

# --- config schema ----------------------------------------------------------


@pytest.mark.parametrize('config', [
    {
        'nebius': {
            'spot_pricing_policy_name': 'spot-max-{platform}'
        }
    },
    {
        'nebius': {
            'region_configs': {
                'eu-north1': {
                    'spot_pricing_policy_name': 'spot-max-gpu-h100-sxm'
                }
            }
        }
    },
])
def test_config_schema_accepts_policy_name(config):
    jsonschema.validate(instance=config, schema=schemas.get_config_schema())


def test_config_schema_rejects_non_string_policy_name():
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(
            instance={'nebius': {
                'spot_pricing_policy_name': 42
            }},
            schema=schemas.get_config_schema(),
        )


@pytest.mark.parametrize(('template', 'expected'), [
    ('spot-max-{platform}', 'spot-max-gpu-h100-sxm'),
    ('{platform}-{platform}', 'gpu-h100-sxm-gpu-h100-sxm'),
    ('my-policy', 'my-policy'),
    ('braces-{other}', 'braces-{other}'),
])
def test_get_spot_pricing_policy_name(template, expected):
    assert nebius_constants.get_spot_pricing_policy_name(template,
                                                         _PLATFORM) == expected


# --- make_deploy_resources_variables + Ray YAML template --------------------


def _deploy_vars(config, use_spot, region_name='eu-north1'):
    resources = resources_lib.Resources(cloud=nebius_cloud.Nebius(),
                                        instance_type=_INSTANCE_TYPE,
                                        use_spot=use_spot).assert_launchable()
    region = mock.MagicMock()
    region.name = region_name
    with skypilot_config.replace_skypilot_config(
            config_utils.Config(config)), \
         mock.patch.object(utils, 'get_project_by_region',
                           return_value='project-id'):
        return nebius_cloud.Nebius().make_deploy_resources_variables(
            resources=resources,
            cluster_name=resources_utils.ClusterName(display_name='c',
                                                     name_on_cloud='c-1234'),
            region=region,
            zones=None,
            num_nodes=1)


_CONFIG = {
    'nebius': {
        'spot_pricing_policy_name': 'spot-max-{platform}',
        'region_configs': {
            'us-central1': {
                'spot_pricing_policy_name': 'us-{platform}',
            },
        },
    },
}


@pytest.mark.parametrize(('config', 'use_spot', 'region', 'expected'), [
    ({}, True, 'eu-north1', None),
    (_CONFIG, False, 'eu-north1', None),
    (_CONFIG, True, 'eu-north1', 'spot-max-gpu-h100-sxm'),
    (_CONFIG, True, 'us-central1', 'us-gpu-h100-sxm'),
])
def test_deploy_vars_resolve_policy_name(config, use_spot, region, expected):
    deploy_vars = _deploy_vars(config, use_spot, region)
    assert deploy_vars['spot_pricing_policy_name'] == expected


def _render_node_config(tmp_path, deploy_vars):
    variables = dict(deploy_vars)
    variables.update({
        'cluster_name_on_cloud': 'c-1234',
        'num_nodes': 1,
        'use_internal_ips': False,
        'ssh_private_key': '~/.ssh/key',
        'ssh_proxy_command': None,
        'docker_image': None,
        'disk_size': 100,
        'authorized_key': 'ssh-ed25519 AAAA',
        'sky_ray_cmd': 'ray',
        'sky_python_cmd': 'python',
        'credentials': {},
    })
    output = tmp_path / 'cluster.yml'
    common_utils.fill_template('nebius-ray.yml.j2', variables, str(output))
    rendered = yaml.safe_load(output.read_text())
    return rendered['available_node_types']['ray_head_default']['node_config']


@pytest.mark.parametrize(('config', 'expected'), [
    (_CONFIG, 'spot-max-gpu-h100-sxm'),
    ({}, None),
])
def test_template_carries_policy_name(tmp_path, config, expected):
    node_config = _render_node_config(tmp_path, _deploy_vars(config, True))
    assert node_config.get('spot_pricing_policy_name') == expected
    # The surrounding keys still parse as before.
    assert node_config['use_spot'] is True
    assert node_config['filesystems'] in (None, [])


# --- get_spot_pricing_policy_id ---------------------------------------------


def _policy(platform=_PLATFORM,
            state='STATE_ACTIVE',
            scheduling_state='SCHEDULING_STATE_ALLOWED'):
    billing = nebius.billing()
    return billing.PricingPolicy(
        metadata=nebius.nebius_common().ResourceMetadata(
            id='pricingpolicy-1', name='spot-max-gpu-h100-sxm'),
        spec=billing.PricingPolicySpec(
            compute_instance_spec=billing.ComputeInstanceSpec(
                v1=billing.ComputeInstanceSpecV1(platform=platform)),
            pricing=billing.PricingMethod(max_price_v1=billing.MaxPriceV1(
                max_price='2.500'))),
        status=billing.PricingPolicyStatus(
            state=billing.PricingPolicyStatus.State[state],
            scheduling_state=billing.PricingPolicyStatus.
            SchedulingState[scheduling_state],
            currency='USD'))


def _request_error(code, message='error'):
    status = mock.MagicMock()
    status.code = code
    status.__str__ = lambda self, _m=message: _m
    return nebius.request_error()(status)


@pytest.fixture
def policy_service(monkeypatch):
    service = mock.Mock()
    monkeypatch.setattr(nebius, 'sdk', lambda: None)
    monkeypatch.setattr(nebius, 'sync_call', lambda response: response)
    monkeypatch.setattr(nebius.billing(), 'PricingPolicyServiceClient',
                        lambda _: service)
    return service


def test_policy_id_for_active_policy(policy_service):
    policy_service.get_by_name.return_value = _policy()

    assert utils.get_spot_pricing_policy_id('project-id',
                                            'spot-max-gpu-h100-sxm',
                                            _PLATFORM) == 'pricingpolicy-1'
    request = policy_service.get_by_name.call_args.args[0]
    assert request.parent_id == 'project-id'
    assert request.name == 'spot-max-gpu-h100-sxm'


def test_missing_policy_falls_back(policy_service, caplog):
    policy_service.get_by_name.side_effect = _request_error(
        nebius.status_code().NOT_FOUND, 'not found')

    assert utils.get_spot_pricing_policy_id('project-id', 'missing',
                                            _PLATFORM) is None
    assert 'not found in Nebius project project-id' in caplog.text


def test_policy_for_other_platform_falls_back(policy_service, caplog):
    policy_service.get_by_name.return_value = _policy(platform='gpu-h200-sxm')

    assert utils.get_spot_pricing_policy_id('project-id', 'p',
                                            _PLATFORM) is None
    assert 'is for platform \'gpu-h200-sxm\'' in caplog.text


def test_other_request_errors_propagate(policy_service):
    err = _request_error(nebius.status_code().PERMISSION_DENIED, 'denied')
    policy_service.get_by_name.side_effect = err

    with pytest.raises(nebius.request_error()) as exc_info:
        utils.get_spot_pricing_policy_id('project-id', 'p', _PLATFORM)
    assert exc_info.value is err


@pytest.mark.parametrize(('state', 'scheduling_state'), [
    ('STATE_ACTIVE', 'SCHEDULING_STATE_BLOCKED'),
    ('STATE_CREATING', 'SCHEDULING_STATE_UNSPECIFIED'),
    ('STATE_UPDATING', 'SCHEDULING_STATE_BLOCKED'),
    ('STATE_DELETING', 'SCHEDULING_STATE_ALLOWED'),
])
def test_blocked_policy_raises(policy_service, state, scheduling_state):
    policy_service.get_by_name.return_value = _policy(
        state=state, scheduling_state=scheduling_state)

    with pytest.raises(utils.SpotPricingPolicyBlockedError,
                       match=r'does not allow new VMs.*2\.500 USD'):
        utils.get_spot_pricing_policy_id('project-id', 'p', _PLATFORM)


# --- launch: pricing model in the CreateInstance request -------------------


@pytest.fixture
def instance_service(monkeypatch):
    compute = nebius.compute()
    service = mock.Mock()
    service.get_by_name.return_value = compute.Instance(
        metadata=nebius.nebius_common().ResourceMetadata(id='instance-id'),
        status=compute.InstanceStatus(
            state=compute.InstanceStatus.InstanceState.STARTING))
    monkeypatch.setattr(nebius, 'sdk', lambda: None)
    monkeypatch.setattr(nebius, 'sync_call', lambda response: response)
    monkeypatch.setattr(compute, 'InstanceServiceClient', lambda _: service)
    monkeypatch.setattr(utils, 'get_project_by_region', lambda _: 'project-id')
    monkeypatch.setattr(utils, 'get_subnet_id', lambda *_: 'subnet-id')
    return service


@pytest.mark.parametrize(('use_spot', 'policy_id', 'expected_mode'), [
    (True, 'pricingpolicy-1', 'spot_pricing_policy'),
    (True, None, 'follows_spot_price'),
    (False, 'pricingpolicy-1', None),
])
def test_launch_serializes_pricing_model(instance_service, use_spot, policy_id,
                                         expected_mode):
    compute = nebius.compute()
    utils.launch(cluster_name_on_cloud='cluster',
                 node_type='head',
                 platform=_PLATFORM,
                 preset=_PRESET,
                 region='eu-north1',
                 image_id_or_family='computeimage-test',
                 disk_size=100,
                 user_data='',
                 associate_public_ip_address=False,
                 filesystems=[],
                 disk_tier=resources_utils.DiskTier.MEDIUM,
                 use_spot=use_spot,
                 spot_pricing_policy_id=policy_id)

    request = instance_service.create.call_args.args[0]
    spec = compute.CreateInstanceRequest.FromString(
        request.SerializeToString()).spec
    assert spec.which_field_in_oneof('pricing_model') == expected_mode
    assert spec.check_presence('preemptible') == use_spot
    if expected_mode == 'spot_pricing_policy':
        assert spec.spot_pricing_policy.id == policy_id


# --- run_instances: one lookup, before any VM is created -------------------


def _provision_config(count, use_spot=True, policy_name='spot-max-h100'):
    node_config = {
        'InstanceType': _INSTANCE_TYPE,
        'ImageId': 'computeimage-test',
        'DiskSize': 100,
        'UserData': '',
        'use_spot': use_spot,
        'disk_tier': resources_utils.DiskTier.MEDIUM,
    }
    if policy_name is not None:
        node_config['spot_pricing_policy_name'] = policy_name
    return provision_common.ProvisionConfig(provider_config={
        'project_id': 'project-id',
        'use_internal_ips': False,
    },
                                            authentication_config={},
                                            docker_config={},
                                            node_config=node_config,
                                            count=count,
                                            tags={},
                                            resume_stopped_nodes=True,
                                            ports_to_open_on_launch=None)


@pytest.fixture
def run_instances_mocks(monkeypatch):
    mocks = mock.Mock()
    mocks.get_spot_pricing_policy_id.return_value = 'pricingpolicy-1'
    mocks.launch.side_effect = lambda **kwargs: 'instance-' + kwargs['node_type'
                                                                    ]
    monkeypatch.setattr(nebius_instance, '_wait_until_no_pending',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(nebius_instance, '_filter_instances',
                        lambda *args, **kwargs: {})
    monkeypatch.setattr(utils, 'get_spot_pricing_policy_id',
                        mocks.get_spot_pricing_policy_id)
    monkeypatch.setattr(utils, 'launch', mocks.launch)
    return mocks


def test_run_instances_resolves_policy_once(run_instances_mocks):
    nebius_instance.run_instances('eu-north1', 'c', 'c-1234',
                                  _provision_config(count=3))

    run_instances_mocks.get_spot_pricing_policy_id.assert_called_once_with(
        'project-id', 'spot-max-h100', _PLATFORM)
    launch_calls = run_instances_mocks.launch.call_args_list
    assert len(launch_calls) == 3
    assert all(call.kwargs['spot_pricing_policy_id'] == 'pricingpolicy-1'
               for call in launch_calls)


@pytest.mark.parametrize(('use_spot', 'policy_name'), [
    (False, 'spot-max-h100'),
    (True, None),
])
def test_run_instances_skips_lookup(run_instances_mocks, use_spot, policy_name):
    nebius_instance.run_instances(
        'eu-north1', 'c', 'c-1234',
        _provision_config(count=1, use_spot=use_spot, policy_name=policy_name))

    run_instances_mocks.get_spot_pricing_policy_id.assert_not_called()
    launch_kwargs = run_instances_mocks.launch.call_args.kwargs
    assert launch_kwargs['spot_pricing_policy_id'] is None


def test_run_instances_blocked_policy_launches_nothing(run_instances_mocks):
    run_instances_mocks.get_spot_pricing_policy_id.side_effect = (
        utils.SpotPricingPolicyBlockedError('blocked'))

    with pytest.raises(utils.SpotPricingPolicyBlockedError):
        nebius_instance.run_instances('eu-north1', 'c', 'c-1234',
                                      _provision_config(count=2))
    run_instances_mocks.launch.assert_not_called()
