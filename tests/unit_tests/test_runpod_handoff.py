"""Offline proof of the RunPod resource contract through the provider boundary."""
import importlib
from unittest import mock

import jsonschema
import pandas as pd
import pytest
import requests
import yaml

from sky import clouds
from sky import skypilot_config
from sky.adaptors import runpod as adaptor
from sky.catalog import common as catalog_common
from sky.provision import common as provision_common
from sky.provision.runpod import instance
from sky.provision.runpod.api import commands
from sky.resources import Resources
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import resources_utils
from sky.utils import schemas


@pytest.fixture(autouse=True)
def offline(monkeypatch):

    def no_network(*args, **kwargs):
        pytest.fail('RunPod contract test attempted network I/O')

    monkeypatch.setattr(requests.sessions.Session, 'request', no_network)
    frame = pd.DataFrame([{
        'InstanceType': '4x_H200-SXM_SECURE',
        'AcceleratorName': 'H200-SXM',
        'AcceleratorCount': 4,
        'vCPUs': 48,
        'MemoryGiB': 752,
        'Price': 18.36,
        'SpotPrice': 9.18,
        'Region': 'US',
        'AvailabilityZone': 'US-TEST-1',
    }])
    monkeypatch.setattr(catalog_common, 'read_catalog',
                        lambda *args, **kwargs: frame)
    catalog = importlib.import_module('sky.catalog.runpod_catalog')
    monkeypatch.setattr(catalog, '_df', frame)
    with skypilot_config.replace_skypilot_config_in_process(
            config_utils.Config()):
        yield frame


def _render(tmp_path,
            *,
            cpus='16+',
            memory='550+',
            cuda=('13.0',),
            spot=False,
            cpu_only=False):
    overrides = {} if cuda is None else {
        'runpod': {
            'allowed_cuda_versions': list(cuda)
        }
    }
    resources = Resources(cloud=clouds.RunPod(),
                          cpus=cpus,
                          memory=memory,
                          accelerators='H200-SXM:4',
                          use_spot=spot,
                          image_id='docker:example/pinned:cuda13')
    resources = resources.copy(_cluster_config_overrides=overrides)
    resources, = Resources.from_yaml_config(resources.to_yaml_config())
    assert resources.cluster_config_overrides == overrides
    if cpu_only:
        resources = resources.copy(instance_type='cpu3c-2-4', accelerators=None)
    else:
        feasible = clouds.RunPod()._get_feasible_launchable_resources(resources)
        assert len(feasible.resources_list) == 1
        resources = feasible.resources_list[0]
    variables = clouds.RunPod().make_deploy_resources_variables(
        resources, resources_utils.ClusterName('test', 'test'),
        clouds.Region('US'), [clouds.Zone('US-TEST-1')], 1)
    variables.update(num_nodes=1,
                     cluster_name_on_cloud='test',
                     disk_size=256,
                     docker_login_config=None,
                     credentials={},
                     ssh_private_key='/test/key')
    output = tmp_path / 'runpod.yml'
    common_utils.fill_template('runpod-ray.yml.j2', variables, str(output))
    rendered = yaml.safe_load(output.read_text())
    return provision_common.ProvisionConfig(
        provider_config=rendered['provider'],
        authentication_config={},
        docker_config={},
        node_config=rendered['available_node_types']['ray_head_default']
        ['node_config'],
        count=1,
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=[8080])


def _run(config, monkeypatch):
    monkeypatch.setattr(
        instance, '_filter_instances',
        mock.Mock(side_effect=[{}, {}, {
            'pod-test': {
                'name': 'test-head',
                'ssh_port': 22
            }
        }]))
    return instance.run_instances('US', 'test', 'test', config)


@pytest.mark.parametrize('spot', [False, True])
@pytest.mark.parametrize('cpus,memory', [('16+', '550+'), ('48', '752'),
                                         ('16+', '12x'), (None, None)])
def test_render_to_provider_preserves_selected_shape(tmp_path, monkeypatch,
                                                     spot, cpus, memory):
    config = _render(tmp_path, cpus=cpus, memory=memory, spot=spot)
    provider = mock.Mock(return_value={'id': 'pod-test'})
    sdk = mock.Mock(create_pod=provider)
    monkeypatch.setattr(adaptor, 'runpod', sdk)
    monkeypatch.setattr(commands, 'create_spot_pod', provider)
    assert _run(config, monkeypatch).created_instance_ids == ['pod-test']
    provider.assert_called_once()
    args = provider.call_args.kwargs
    assert args['gpu_type_id'] == 'NVIDIA H200'
    assert args['gpu_count'] == 4
    assert args['min_vcpu_count'] == 48
    assert args['min_memory_in_gb'] == 808
    assert args['allowed_cuda_versions'] == ['13.0']
    assert args['image_name'] == 'example/pinned:cuda13'
    assert args['data_center_id'] == 'US-TEST-1'
    assert args['container_disk_in_gb'] == 256
    assert ('bid_per_gpu' in args) is spot
    sdk.get_gpu.assert_not_called()  # GPU VRAM is not host RAM.


@pytest.mark.parametrize('column', ['vCPUs', 'MemoryGiB'])
@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), 0, -1])
def test_unknown_or_invalid_shape_cannot_render(tmp_path, offline, column,
                                                value):
    offline[column] = value
    with pytest.raises(ValueError, match='known positive CPU and host RAM'):
        _render(tmp_path, cpus=None, memory=None)


@pytest.mark.parametrize('key', ['MinVCPUCount', 'MinMemoryInGB'])
@pytest.mark.parametrize('value', [None, 0, -1, 1.5, True])
def test_missing_or_invalid_handoff_fails_before_provider_effects(
        tmp_path, monkeypatch, key, value):
    config = _render(tmp_path)
    if value is None:
        config.node_config.pop(key)
    else:
        config.node_config[key] = value
    sdk = mock.Mock()
    monkeypatch.setattr(adaptor, 'runpod', sdk)
    with pytest.raises(ValueError, match='host CPU and RAM minima'):
        _run(config, monkeypatch)
    assert not sdk.mock_calls


@pytest.mark.parametrize(
    'versions',
    [[], ['13'], ['13.0\n'], [13.0], ['13.0', '13.0'], '13.0', None])
def test_cuda_config_rejects_malformed_values(versions):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({'runpod': {
            'allowed_cuda_versions': versions
        }}, schemas.get_config_schema())


@pytest.mark.parametrize('spot', [False, True])
def test_no_eligible_cuda_keeps_filters_and_propagates_failure(
        tmp_path, monkeypatch, spot):
    config = _render(tmp_path, spot=spot)
    provider = mock.Mock(side_effect=RuntimeError('No eligible CUDA host'))
    monkeypatch.setattr(adaptor, 'runpod', mock.Mock(create_pod=provider))
    monkeypatch.setattr(commands, 'create_spot_pod', provider)
    with pytest.raises(RuntimeError, match='No eligible CUDA host'):
        _run(config, monkeypatch)
    provider.assert_called_once()
    assert provider.call_args.kwargs['allowed_cuda_versions'] == ['13.0']
    assert provider.call_args.kwargs['min_memory_in_gb'] == 808


def test_unset_cuda_adds_no_filter(tmp_path, monkeypatch):
    config = _render(tmp_path, cuda=None)
    sdk = mock.Mock()
    sdk.create_pod.return_value = {'id': 'pod-test'}
    monkeypatch.setattr(adaptor, 'runpod', sdk)
    _run(config, monkeypatch)
    assert 'AllowedCUDAVersions' not in config.node_config
    assert 'allowed_cuda_versions' not in sdk.create_pod.call_args.kwargs


def test_fractional_shape_rounds_up_in_provider_units(tmp_path, offline):
    offline['vCPUs'] = 48.25
    offline['MemoryGiB'] = 1.25
    config = _render(tmp_path, cpus=None, memory=None)
    assert config.node_config['MinVCPUCount'] == 49
    assert config.node_config['MinMemoryInGB'] == 2


@pytest.mark.parametrize('versions', [[], '13.0', ['13.0\n'], [13.0]])
def test_invalid_rendered_cuda_fails_before_provider_effects(
        tmp_path, monkeypatch, versions):
    config = _render(tmp_path)
    config.node_config['AllowedCUDAVersions'] = versions
    sdk = mock.Mock()
    monkeypatch.setattr(adaptor, 'runpod', sdk)
    with pytest.raises(ValueError, match='allowed_cuda_versions'):
        _run(config, monkeypatch)
    assert not sdk.mock_calls


def test_global_cuda_and_task_override(tmp_path):
    with skypilot_config.replace_skypilot_config_in_process(
            config_utils.Config({'runpod': {
                'allowed_cuda_versions': ['12.9']
            }})):
        assert _render(
            tmp_path, cuda=None).node_config['AllowedCUDAVersions'] == ['12.9']
        assert _render(tmp_path,
                       cuda=('13.0',
                             '13.1')).node_config['AllowedCUDAVersions'] == [
                                 '13.0', '13.1'
                             ]


def test_cpu_instance_path_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(clouds.RunPod, 'get_accelerators_from_instance_type',
                        lambda *args: None)
    monkeypatch.setattr(clouds.RunPod, 'instance_type_to_hourly_cost',
                        lambda *args, **kwargs: 0.1)
    config = _render(tmp_path, cpu_only=True)
    sdk = mock.Mock()
    sdk.create_pod.return_value = {'id': 'pod-test'}
    monkeypatch.setattr(adaptor, 'runpod', sdk)
    _run(config, monkeypatch)
    args = sdk.create_pod.call_args.kwargs
    assert args['instance_id'] == 'cpu3c-2-4'
    assert all(key not in args for key in [
        'gpu_count', 'min_vcpu_count', 'min_memory_in_gb',
        'allowed_cuda_versions'
    ])


@pytest.mark.parametrize('spot', [False, True])
def test_actual_sdk_and_spot_graphql_preserve_total_requirements(
        tmp_path, monkeypatch, spot):
    sdk = pytest.importorskip('runpod')
    ctl = importlib.import_module('runpod.api.ctl_commands')
    graphql = importlib.import_module('runpod.api.graphql')
    calls = []

    def query(body, **kwargs):
        calls.append(body)
        return {
            'data': {
                'podFindAndDeployOnDemand': {
                    'id': 'pod-test'
                },
                'podRentInterruptable': {
                    'id': 'pod-test'
                }
            }
        }

    monkeypatch.setattr(adaptor, 'runpod', sdk)
    monkeypatch.setattr(sdk, 'get_gpu', lambda *args, **kwargs: {})
    monkeypatch.setattr(ctl, 'get_gpu', lambda *args, **kwargs: {})
    monkeypatch.setattr(ctl, 'run_graphql_query', query)
    monkeypatch.setattr(graphql, 'run_graphql_query', query)
    _run(_render(tmp_path, spot=spot), monkeypatch)
    assert len(calls) == 1
    assert 'minVcpuCount: 48' in calls[0]
    assert 'minMemoryInGb: 808' in calls[0]
    assert 'gpuCount: 4' in calls[0]
    assert 'allowedCudaVersions: ["13.0"]' in calls[0]
