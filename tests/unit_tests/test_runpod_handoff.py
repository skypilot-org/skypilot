"""Offline RunPod resource contract through the provider boundary."""
# The test exercises the provider's internal selection entry point.
# pylint: disable=protected-access
import importlib
import json
import math
import os
from pathlib import Path
import pickle
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

import jsonschema
import networkx as nx
import pandas as pd
import pytest
import requests
import yaml

from sky import clouds
from sky import dag as dag_lib
from sky import exceptions
from sky import optimizer
from sky import skypilot_config
from sky import task as task_lib
from sky.adaptors import runpod as adaptor
from sky.backends import cloud_vm_ray_backend
from sky.catalog import common as catalog_common
from sky.provision import common as provision_common
from sky.provision.runpod import instance
from sky.provision.runpod.api import commands
from sky.resources import Resources
from sky.server import metrics
from sky.utils import accelerator_registry
from sky.utils import common_utils
from sky.utils import config_utils
from sky.utils import resources_utils
from sky.utils import schemas
from sky.utils import status_lib


@pytest.fixture(autouse=True, name='offline')
def offline_catalog(monkeypatch):

    def no_network(*_args, **_kwargs):
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
    monkeypatch.setattr(accelerator_registry, '_accelerator_df',
                        frame.assign(Clouds='RunPod'))
    selection_cache = adaptor.get_gpu_host_quote
    selection_cache.cache_clear()
    with skypilot_config.replace_skypilot_config_in_process(
            config_utils.Config()):
        yield frame
    selection_cache.cache_clear()


def _render(tmp_path,
            *,
            cpus='16+',
            memory='550+',
            cuda=('13.0',),
            spot=False,
            cpu_only=False,
            accelerators='H200-SXM:4'):
    overrides = {} if cuda is None else {
        'runpod': {
            'allowed_cuda_versions': list(cuda)
        }
    }
    resources = Resources(cloud=clouds.RunPod(),
                          cpus=cpus,
                          memory=memory,
                          accelerators=accelerators,
                          use_spot=spot,
                          image_id='docker:example/pinned:cuda13')
    resources = resources.copy(_cluster_config_overrides=overrides)
    resources, = Resources.from_yaml_config(resources.to_yaml_config())
    resources.validate()
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
@pytest.mark.parametrize('cpus,memory',
                         [('16+', '550+'),
                          ('48', str(752_000_000_000 / 1_073_741_824)),
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
    assert args['min_memory_in_gb'] == 752
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
    assert provider.call_args.kwargs['min_memory_in_gb'] == 752


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
@pytest.mark.parametrize('native_memory', [591, 752])
def test_actual_sdk_and_spot_graphql_preserve_total_requirements(
        tmp_path, monkeypatch, offline, spot, native_memory):
    sdk = pytest.importorskip('runpod')
    fetcher = importlib.import_module('sky.catalog.data_fetchers.fetch_runpod')
    # Start with the real catalog fetcher's unconverted provider data, not a
    # hand-written fixture asserting that nominal GB was already GiB.
    shape = fetcher.get_gpu_info('H200-SXM', {
        'displayName': 'H200 SXM',
        'manufacturer': 'NVIDIA',
        'memoryInGb': 141,
    }, 4)
    assert shape['vCPUs'] == 48
    assert shape['MemoryGiB'] == 752
    offline['vCPUs'] = shape['vCPUs']
    offline['MemoryGiB'] = native_memory
    ctl = importlib.import_module('runpod.api.ctl_commands')
    graphql = importlib.import_module('runpod.api.graphql')
    calls = []

    def query(body, **_kwargs):
        calls.append(body)
        # A native-sized host must remain eligible. Inflating the native 752
        # to 808 reproduces the old no-eligible-host failure at this boundary.
        memory = int(body.split('minMemoryInGb: ')[1].split(',')[0])
        if memory > native_memory:
            raise RuntimeError('No eligible host meets inflated memory floor')
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
    assert f'minMemoryInGb: {native_memory}' in calls[0]
    assert 'gpuCount: 4' in calls[0]
    assert 'allowedCudaVersions: ["13.0"]' in calls[0]


@pytest.mark.parametrize('spot', [False, True])
@pytest.mark.parametrize('native_memory,eligible', [(590, False), (591, True)])
def test_sky_gib_floor_uses_conservative_provider_gb(offline, spot,
                                                     native_memory, eligible):
    offline['MemoryGiB'] = native_memory
    resources = Resources(cloud=clouds.RunPod(),
                          accelerators='H200-SXM:4',
                          cpus='16+',
                          memory='550+',
                          use_spot=spot)
    result = clouds.RunPod()._get_feasible_launchable_resources(resources)
    assert bool(result.resources_list) is eligible
    assert not result.fuzzy_candidate_list


def test_normalized_view_keeps_native_shape_and_cpu_rows(offline, monkeypatch):
    catalog = importlib.import_module('sky.catalog.runpod_catalog')
    cpu = offline.iloc[0].to_dict()
    cpu.update(InstanceType='cpu3c-2-4',
               AcceleratorName=None,
               AcceleratorCount=0,
               vCPUs=2,
               MemoryGiB=4)
    frame = pd.concat([offline, pd.DataFrame([cpu])], ignore_index=True)
    monkeypatch.setattr(catalog, '_df', frame)
    before = frame.copy(deep=True)
    for _ in range(2):
        assert catalog.get_vcpus_mem_from_instance_type(
            '4x_H200-SXM_SECURE') == (48, 752_000_000_000 / 1_073_741_824)
        assert catalog.get_native_gpu_host_resources('4x_H200-SXM_SECURE') == (
            48, 752)
        assert catalog.get_vcpus_mem_from_instance_type('cpu3c-2-4') == (2, 4)
        assert catalog.get_default_instance_type(cpus='2',
                                                 memory='4') == ('cpu3c-2-4')
    pd.testing.assert_frame_equal(frame, before)


def _singleton(frame):
    frame['InstanceType'] = '1x_H200-SXM_SECURE'
    frame['AcceleratorCount'] = 1
    frame['vCPUs'] = 12
    frame['MemoryGiB'] = 188
    frame['Price'] = 4.59


def _host_quote(**overrides):
    # Retained constrained provider response: gpuCount=1, CPU>=16, GB>=207.
    return dict(uninterruptablePrice=4.59,
                minVcpu=20,
                minMemory=251,
                stockStatus='Low',
                availableGpuCounts=None,
                **overrides)


@pytest.mark.parametrize('offer_cpu,offer_gb', [(20, 251), (24, 377)])
@pytest.mark.parametrize('accelerators', ['H200-SXM:1', 'H200:1'])
def test_stronger_singleton_survives_yaml_and_actual_sdk(
        tmp_path, monkeypatch, offline, offer_cpu, offer_gb, accelerators):
    sdk = pytest.importorskip('runpod')
    ctl = importlib.import_module('runpod.api.ctl_commands')
    catalog = importlib.import_module('sky.catalog.runpod_catalog')
    monkeypatch.setattr(
        accelerator_registry, '_accelerator_df',
        pd.DataFrame({
            'AcceleratorName': ['H200', 'H200-SXM'],
            'Clouds': ['AWS,GCP', 'RunPod']
        }))
    _singleton(offline)
    before = offline.copy(deep=True)
    quote = _host_quote()
    quote.update(minVcpu=offer_cpu, minMemory=offer_gb)
    lookup = mock.Mock(return_value=quote)
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    calls = []
    monkeypatch.setattr(adaptor, 'runpod', sdk)
    monkeypatch.setattr(ctl, 'get_gpu', lambda *args, **kwargs: {})
    monkeypatch.setattr(
        ctl, 'run_graphql_query', lambda body: (calls.append(body) or {
            'data': {
                'podFindAndDeployOnDemand': {
                    'id': 'pod-test'
                }
            }
        }))
    config = _render(tmp_path, memory='192+', accelerators=accelerators)
    assert config.node_config['InstanceType'] == '1x_H200-SXM_SECURE'
    assert config.node_config['MinVCPUCount'] == 16
    assert config.node_config['MinMemoryInGB'] == 207
    _run(config, monkeypatch)
    assert len(calls) == 1
    assert 'gpuCount: 1' in calls[0]
    assert 'minVcpuCount: 16' in calls[0]
    assert 'minMemoryInGb: 207' in calls[0]
    assert lookup.call_args.args[:5] == ('NVIDIA H200', 1, True, 16, 207)
    pd.testing.assert_frame_equal(offline, before)
    assert catalog.get_native_gpu_host_resources('1x_H200-SXM_SECURE') == (12,
                                                                           188)


@pytest.mark.parametrize('bad', [
    None, {}, {
        'minVcpu': 15
    }, {
        'minMemory': 206
    }, {
        'stockStatus': None
    }, {
        'stockStatus': 'None'
    }, {
        'uninterruptablePrice': None
    }, {
        'uninterruptablePrice': math.nan
    }, {
        'minVcpu': True
    }, {
        'availableGpuCounts': []
    }, {
        'availableGpuCounts': [2, 4]
    }, {
        'availableGpuCounts': 1
    }, {
        'availableGpuCounts': [True]
    }
])
def test_unavailable_stronger_host_is_not_feasible(monkeypatch, offline, bad):
    _singleton(offline)
    quote = None if bad is None else {**_host_quote(), **bad}
    if bad == {}:
        quote = {}
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lambda *args: quote)
    resources = Resources(cloud=clouds.RunPod(),
                          accelerators='H200-SXM:1',
                          cpus='16+',
                          memory='192+')
    assert not clouds.RunPod()._get_feasible_launchable_resources(
        resources).resources_list


@pytest.mark.parametrize('gib,gb', [
    ('192', 207),
    ('550', 591),
    ('0.931322574615478515625', 1),
    ('0.931322574615478515624', 1),
    ('0.931322574615478515626', 2),
])
def test_requested_gib_rounds_up_once(gib, gb):
    catalog = importlib.import_module('sky.catalog.runpod_catalog')
    assert catalog._provider_memory_gb(gib) == gb


def test_sized_resources_round_trip_and_price_bound(monkeypatch, offline):
    _singleton(offline)
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote',
                        lambda *args: _host_quote())
    resources = Resources(cloud=clouds.RunPod(),
                          accelerators='H200-SXM:1',
                          cpus='16+',
                          memory='192+',
                          region='US')
    chosen, = clouds.RunPod()._get_feasible_launchable_resources(
        resources).resources_list
    restored, = Resources.from_yaml_config(chosen.to_yaml_config())
    restored.validate()
    assert restored.instance_type == '1x_H200-SXM_SECURE--16vcpu-207gb-4.59usd'
    assert restored.accelerators == {'H200-SXM': 1}
    assert restored.cpus == '16'
    assert restored.memory == '192+'
    assert restored.cloud.instance_type_to_hourly_cost(restored.instance_type,
                                                       False,
                                                       region='US') == 4.59
    assert not clouds.RunPod()._get_feasible_launchable_resources(
        resources.copy(max_hourly_cost=4.58)).resources_list
    assert len(clouds.RunPod()._get_feasible_launchable_resources(
        resources.copy(max_hourly_cost=4.59)).resources_list) == 1


def test_quote_loss_before_render_never_creates_pod(tmp_path, monkeypatch,
                                                    offline):
    _singleton(offline)
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote',
                        mock.Mock(side_effect=[_host_quote(), None]))
    with pytest.raises(exceptions.ResourcesUnavailableError,
                       match='No current RunPod host quote'):
        _render(tmp_path, memory='192+', accelerators='H200-SXM:1')


@pytest.mark.parametrize('quote', [None, {'uninterruptablePrice': 5.0}])
@pytest.mark.parametrize('previous_status', [
    None, status_lib.ClusterStatus.INIT, status_lib.ClusterStatus.UP,
    status_lib.ClusterStatus.STOPPED
])
def test_render_quote_rejection_uses_actual_capacity_fallback(
        tmp_path, monkeypatch, offline, quote, previous_status):
    _singleton(offline)
    rejected = None if quote is None else _host_quote() | quote
    lookup = mock.Mock(side_effect=[_host_quote(), rejected])
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    request = Resources(cloud=clouds.RunPod(),
                        cpus='16+',
                        memory='192+',
                        accelerators='H200-SXM:1',
                        region='US',
                        max_hourly_cost=4.59,
                        image_id='docker:example/pinned:cuda13')
    selected, = request.cloud._get_feasible_launchable_resources(
        request).resources_list
    if previous_status is not None:
        selected = selected.copy(zone='US-TEST-1')
    provisioner = cloud_vm_ray_backend.RetryingVmProvisioner(
        str(tmp_path),
        None,
        None,
        set(),
        tmp_path / 'unused.whl',
        'unused',
        extra_launch_context={})
    created = mock.Mock(side_effect=AssertionError('rejected quote allocated'))
    monkeypatch.setattr(cloud_vm_ray_backend.provisioner, 'bulk_provision',
                        created)
    monkeypatch.setattr(cloud_vm_ray_backend.rich_utils, 'force_update_status',
                        lambda *args, **kwargs: None)
    task = task_lib.Task().set_resources(request)
    # Real Resources -> write_cluster_config -> RunPod renderer, caught by the
    # real zone loop. No cloud call or state publication should be reached.
    expected = ('Failed to acquire resources'
                if previous_status is None else None)
    with pytest.raises(exceptions.ResourcesUnavailableError,
                       match=expected) as caught:
        provisioner._retry_zones(selected,
                                 1, {request},
                                 dryrun=True,
                                 stream_logs=False,
                                 cluster_name='offline-quote-rejection',
                                 cloud_user_identity=None,
                                 prev_cluster_status=previous_status,
                                 prev_handle=None,
                                 prev_cluster_ever_up=previous_status
                                 in (status_lib.ClusterStatus.UP,
                                     status_lib.ClusterStatus.STOPPED),
                                 skip_if_config_hash_matches=None,
                                 volume_mounts=None,
                                 task=task)
    assert caught.value.no_failover is (previous_status is not None)
    assert lookup.call_count == 2
    created.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_restored_sized_instance_requires_current_offer(monkeypatch, offline):
    _singleton(offline)
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lambda *args: None)
    resources = Resources(
        cloud=clouds.RunPod(),
        instance_type='1x_H200-SXM_SECURE--16vcpu-207gb-4.59usd')
    assert not clouds.RunPod()._get_feasible_launchable_resources(
        resources).resources_list


@pytest.mark.parametrize('kwargs', [
    dict(cpus='16'),
    dict(memory='192'),
    dict(memory='12x'),
    dict(use_spot=True)
])
def test_stronger_host_does_not_relax_exact_or_spot_requests(
        monkeypatch, offline, kwargs):
    _singleton(offline)
    lookup = mock.Mock(side_effect=AssertionError('Unexpected live lookup'))
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    resources = Resources(cloud=clouds.RunPod(),
                          accelerators='H200-SXM:1',
                          **{
                              'cpus': '16+',
                              'memory': '192+',
                              **kwargs
                          })
    assert not clouds.RunPod()._get_feasible_launchable_resources(
        resources).resources_list
    lookup.assert_not_called()


def test_multi_gpu_keeps_static_contract_without_unproved_quote_units(
        monkeypatch):
    lookup = mock.Mock(side_effect=AssertionError('Unexpected live lookup'))
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    resources = Resources(cloud=clouds.RunPod(),
                          accelerators='H200-SXM:4',
                          cpus='64+',
                          memory='800+')
    assert not clouds.RunPod()._get_feasible_launchable_resources(
        resources).resources_list
    chosen, = clouds.RunPod()._get_feasible_launchable_resources(
        resources.copy(cpus='16+', memory='550+')).resources_list
    assert chosen.instance_type == '4x_H200-SXM_SECURE'
    assert chosen.cloud.instance_type_to_hourly_cost(chosen.instance_type,
                                                     False) == 18.36
    lookup.assert_not_called()


@pytest.mark.parametrize('failure', ['http', 'graphql', 'null', 'malformed'])
def test_constrained_quote_failure_is_not_an_offer(monkeypatch, failure):
    adaptor.get_gpu_host_quote.cache_clear()
    monkeypatch.setattr(adaptor, '_get_api_key', lambda: 'test-key')
    response = mock.Mock()
    response.json.return_value = {
        'data': {
            'gpuTypes': [{
                'lowestPrice': _host_quote()
            }]
        }
    }
    if failure == 'http':
        response.raise_for_status.side_effect = requests.HTTPError('private')
    elif failure == 'graphql':
        response.json.return_value = {'errors': [{'message': 'private'}]}
    elif failure == 'null':
        response.json.return_value = {
            'data': {
                'gpuTypes': [{
                    'lowestPrice': None
                }]
            }
        }
    else:
        response.json.return_value = {'data': None}
    monkeypatch.setattr(requests, 'post', lambda *args, **kwargs: response)
    assert adaptor.get_gpu_host_quote('NVIDIA H200', 1, True, 16, 207,
                                      'US') is None
    adaptor.get_gpu_host_quote.cache_clear()


def test_constrained_quote_request_is_read_only_and_bounded(monkeypatch):
    adaptor.get_gpu_host_quote.cache_clear()
    monkeypatch.setattr(adaptor, '_get_api_key', lambda: 'test-key')
    response = mock.Mock()
    response.json.return_value = {
        'data': {
            'gpuTypes': [{
                'lowestPrice': _host_quote()
            }]
        }
    }
    post = mock.Mock(return_value=response)
    monkeypatch.setattr(requests, 'post', post)
    assert adaptor.get_gpu_host_quote('NVIDIA H200', 1, True, 16, 207,
                                      'US') == _host_quote()
    body = post.call_args.kwargs['json']['query']
    assert body.startswith('query ')
    for field in ('gpuCount: 1', 'secureCloud: true', 'minVcpuCount: 16',
                  'minMemoryInGb: 207', 'countryCode: "US"'):
        assert field in body
    assert post.call_args.kwargs['timeout'] == 10
    adaptor.get_gpu_host_quote('NVIDIA H200', 1, True, 16, 207, 'US')
    assert post.call_count == 1
    adaptor.get_gpu_host_quote.cache_clear()


@pytest.mark.parametrize('later_quote', [None, {'uninterruptablePrice': 5.0}])
def test_successful_provision_uses_already_rendered_resources(
        tmp_path, monkeypatch, offline, later_quote):
    _singleton(offline)
    lookup = mock.Mock(return_value=_host_quote())
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    request = Resources(cloud=clouds.RunPod(),
                        cpus='16+',
                        memory='192+',
                        accelerators='H200-SXM:1',
                        region='US',
                        max_hourly_cost=4.59,
                        image_id='docker:example/pinned:cuda13')
    selected, = request.cloud._get_feasible_launchable_resources(
        request).resources_list
    backend = cloud_vm_ray_backend
    monkeypatch.setattr(backend.backend_utils.auth_utils,
                        'get_or_generate_keys', lambda:
                        ('/offline/private-key', '/offline/public-key'))
    monkeypatch.setattr(backend.backend_utils,
                        '_get_yaml_path_from_cluster_name',
                        lambda *args: str(tmp_path / 'rendered.yaml'))
    monkeypatch.setattr(backend.backend_utils.sky_check,
                        'get_cloud_credential_file_mounts',
                        lambda *args, **kwargs: {})
    monkeypatch.setattr(backend.backend_utils, '_add_auth_to_cluster_config',
                        lambda *args: None)
    monkeypatch.setattr(backend.backend_utils, '_optimize_file_mounts',
                        lambda *args: None)
    monkeypatch.setattr(backend.global_user_state, 'get_cluster_yaml_str',
                        lambda *args: None)
    for name in ('set_cluster_yaml', 'add_or_update_cluster',
                 'add_cluster_event', 'set_owner_identity_for_cluster'):
        monkeypatch.setattr(backend.global_user_state, name,
                            lambda *args, **kwargs: None)
    monkeypatch.setattr(backend, 'CloudVmRayResourceHandle', SimpleNamespace)
    monkeypatch.setattr(backend.rich_utils, 'force_update_status',
                        lambda *args, **kwargs: None)
    record = mock.sentinel.provision_record

    def allocated(*_args, **_kwargs):
        lookup.return_value = None if later_quote is None else _host_quote(
        ) | later_quote
        return record

    created = mock.Mock(side_effect=allocated)
    monkeypatch.setattr(backend.provisioner, 'bulk_provision', created)
    cleanup = mock.Mock()
    monkeypatch.setattr(backend.CloudVmRayBackend, 'post_teardown_cleanup',
                        cleanup)
    provisioner = backend.RetryingVmProvisioner(str(tmp_path),
                                                None,
                                                None,
                                                set(),
                                                tmp_path / 'unused.whl',
                                                'unused',
                                                extra_launch_context={})
    result = provisioner._retry_zones(
        selected,
        1, {request},
        dryrun=False,
        stream_logs=False,
        cluster_name='offline-quote-after-create',
        cloud_user_identity=None,
        prev_cluster_status=None,
        prev_handle=None,
        prev_cluster_ever_up=False,
        skip_if_config_hash_matches=None,
        volume_mounts=None,
        task=task_lib.Task().set_resources(request))
    assert result['provision_record'] is record
    assert result['resources_vars'] == {'custom_resources': '{"H200-SXM":1}'}
    # Selection and pre-allocation rendering only.
    assert lookup.call_count == 2
    created.assert_called_once()
    cleanup.assert_not_called()


@pytest.mark.parametrize('price', [4.59, 6.125, 1e-8])
def test_selected_estimate_accounting_and_optimizer_need_no_quote(
        monkeypatch, offline, price):
    _singleton(offline)
    lookup = mock.Mock(return_value=_host_quote() |
                       {'uninterruptablePrice': price})
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    request = Resources(cloud=clouds.RunPod(),
                        accelerators='H200-SXM:1',
                        cpus='16+',
                        memory='192+',
                        region='US')
    selected, = request.cloud._get_feasible_launchable_resources(
        request).resources_list
    lookup.reset_mock()
    lookup.side_effect = AssertionError(
        'Accounting must not query available hosts')
    restored, = Resources.from_yaml_config(
        json.loads(json.dumps(selected.to_yaml_config())))
    for resource in (selected, selected.copy(), restored,
                     pickle.loads(pickle.dumps(restored))):
        assert resource.get_cost(3600) == price
    monkeypatch.setattr(
        metrics.global_user_state, 'get_clusters', lambda: [{
            'status': 'UP',
            'handle': SimpleNamespace(launched_resources=restored)
        }])
    assert metrics.BurnRateCollector()._compute_total() == price
    with dag_lib.Dag() as dag:
        workload = task_lib.Task('priced-host').set_resources(restored)
    optimizer.Optimizer._add_dummy_source_sink_nodes(dag)
    topo = list(nx.topological_sort(dag.get_graph()))
    costs = {
        node: {
            next(iter(node.resources)): restored.get_cost(3600)
                                        if node is workload else 0.0
        } for node in topo
    }
    plan, total = optimizer.Optimizer._optimize_by_dp(topo, costs)
    assert plan[workload] is restored
    assert total == price
    lookup.assert_not_called()


def test_selected_estimate_round_trip_in_fresh_process(monkeypatch, offline):
    _singleton(offline)
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote',
                        lambda *args: _host_quote())
    request = Resources(cloud=clouds.RunPod(),
                        accelerators='H200-SXM:1',
                        cpus='16+',
                        memory='192+',
                        region='US')
    selected, = request.cloud._get_feasible_launchable_resources(
        request).resources_list
    # A new interpreter has no in-memory quote cache. Only the task/resource
    # serialization carries the estimate; external I/O is forbidden there.
    script = """
import json, pickle, socket, sys
import pandas as pd

def denied(*args, **kwargs):
    raise AssertionError('Fresh-process accounting attempted provider I/O')
socket.socket.connect = denied
socket.create_connection = denied
from sky.catalog import common
common.read_catalog = lambda *args, **kwargs: pd.DataFrame(json.loads(sys.argv[1]))
from sky.adaptors import runpod
runpod.get_gpu_host_quote = denied
runpod._get_gpu_host_quote = denied
from sky.resources import Resources
resource, = Resources.from_yaml_config(json.loads(sys.stdin.read()))
for restored in (resource, resource.copy(), pickle.loads(pickle.dumps(resource))):
    assert restored.get_cost(3600) == 4.59
print('DURABLE_ESTIMATE_OK')
"""
    source_root = str(Path(clouds.runpod.__file__).parents[2])
    result = subprocess.run(
        [sys.executable, '-c', script,
         offline.to_json(orient='records')],
        input=json.dumps(selected.to_yaml_config()),
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
        cwd=source_root,
        env=dict(os.environ,
                 PYTHONPATH=source_root,
                 CUDA_VISIBLE_DEVICES='',
                 PYTHONDONTWRITEBYTECODE='1',
                 SKYPILOT_DISABLE_USAGE_COLLECTION='1'))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == 'DURABLE_ESTIMATE_OK'


@pytest.mark.parametrize(
    'changed',
    [None, {
        'stockStatus': 'OutOfStock'
    }, {
        'uninterruptablePrice': 5.0
    }])
def test_durable_estimate_does_not_authorize_new_allocation(
        monkeypatch, offline, changed):
    _singleton(offline)
    lookup = mock.Mock(return_value=_host_quote())
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    request = Resources(cloud=clouds.RunPod(),
                        accelerators='H200-SXM:1',
                        cpus='16+',
                        memory='192+',
                        region='US',
                        max_hourly_cost=4.59)
    selected, = request.cloud._get_feasible_launchable_resources(
        request).resources_list
    restored, = Resources.from_yaml_config(selected.to_yaml_config())
    lookup.return_value = None if changed is None else _host_quote() | changed
    assert restored.get_cost(3600) == 4.59
    assert not restored.cloud._get_feasible_launchable_resources(
        restored).resources_list
    with pytest.raises(exceptions.ResourcesUnavailableError):
        restored.cloud.make_deploy_resources_variables(
            restored, resources_utils.ClusterName('offline', 'offline'),
            clouds.Region('US'), [clouds.Zone('US-TEST-1')], 1)
    # Selection, renewed feasibility, render gate.
    assert lookup.call_count == 3


def test_admission_quote_bypasses_selection_cache(monkeypatch, offline):
    _singleton(offline)
    adaptor.get_gpu_host_quote.cache_clear()
    monkeypatch.setattr(adaptor, '_get_api_key', lambda: 'test-key')
    responses = []
    for quote in (_host_quote(), None):
        response = mock.Mock()
        response.json.return_value = {
            'data': {
                'gpuTypes': [{
                    'lowestPrice': quote
                }]
            }
        }
        responses.append(response)
    post = mock.Mock(side_effect=responses)
    monkeypatch.setattr(requests, 'post', post)
    try:
        request = Resources(cloud=clouds.RunPod(),
                            accelerators='H200-SXM:1',
                            cpus='16+',
                            memory='192+',
                            region='US')
        selected, = request.cloud._get_feasible_launchable_resources(
            request).resources_list
        assert selected.get_cost(3600) == 4.59
        assert post.call_count == 1
        with pytest.raises(exceptions.ResourcesUnavailableError):
            selected.cloud.make_deploy_resources_variables(
                selected, resources_utils.ClusterName('offline', 'offline'),
                clouds.Region('US'), [clouds.Zone('US-TEST-1')], 1)
        assert post.call_count == 2  # Bypass the selection cache's TTL.
    finally:
        adaptor.get_gpu_host_quote.cache_clear()


@pytest.mark.parametrize(
    'suffix', ['-0usd', '--1usd', '-nanusd', '-infusd', '-1e999usd', ''])
def test_invalid_or_unpriced_sized_identity_is_not_an_estimate(
        monkeypatch, offline, suffix):
    _singleton(offline)
    lookup = mock.Mock(
        side_effect=AssertionError('Invalid price queried provider'))
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    with pytest.raises(ValueError):
        Resources(cloud=clouds.RunPod(),
                  instance_type='1x_H200-SXM_SECURE--16vcpu-207gb' +
                  suffix).validate()
    lookup.assert_not_called()


def test_selection_estimate_remains_distinct_from_admission_quote(
        monkeypatch, offline):
    _singleton(offline)
    lookup = mock.Mock(return_value=_host_quote())
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    request = Resources(cloud=clouds.RunPod(),
                        accelerators='H200-SXM:1',
                        cpus='16+',
                        memory='192+',
                        region='US',
                        max_hourly_cost=6.0)
    selected, = request.cloud._get_feasible_launchable_resources(
        request).resources_list
    lookup.return_value = _host_quote() | {'uninterruptablePrice': 5.25}
    values = selected.cloud.make_deploy_resources_variables(
        selected, resources_utils.ClusterName('offline', 'offline'),
        clouds.Region('US'), [clouds.Zone('US-TEST-1')], 1)
    assert values['instance_type'] == '1x_H200-SXM_SECURE'
    assert values['bid_per_gpu'] == '5.25'
    assert selected.get_cost(3600) == 4.59  # Estimate, not a billing receipt.
    assert lookup.call_count == 2


@pytest.mark.parametrize('changed_price', [4.59, 5.25])
@pytest.mark.parametrize('regions', [('US',), ('US', 'EU')])
def test_optimizer_keeps_failed_host_blocked_after_requote(
        monkeypatch, offline, changed_price, regions):
    _singleton(offline)
    if len(regions) > 1:
        offline.loc[1] = offline.iloc[0]
        offline.loc[1, 'Region'] = regions[1]
        offline.loc[1, 'AvailabilityZone'] = 'EU-TEST-1'
    lookup = mock.Mock(return_value=_host_quote())
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    monkeypatch.setattr(optimizer.sky_check,
                        'get_cached_enabled_clouds_or_refresh',
                        lambda *args, **kwargs: [clouds.RunPod()])
    monkeypatch.setattr(resources_utils, 'need_to_query_reservations',
                        lambda: False)
    with dag_lib.Dag() as dag:
        task = task_lib.Task('quote-changing-failover').set_resources(
            Resources(cloud=clouds.RunPod(),
                      accelerators='H200-SXM:1',
                      cpus='16+',
                      memory='192+'))
    optimizer.Optimizer.optimize(dag, quiet=True)
    failed = task.best_resources
    assert failed is not None and failed.region in regions
    lookup.return_value = _host_quote() | {
        'uninterruptablePrice': changed_price
    }
    adaptor.get_gpu_host_quote.cache_clear(
    )  # Deterministic selection TTL expiry.
    task.best_resources = None
    if len(regions) == 1:
        with pytest.raises(exceptions.ResourcesUnavailableError):
            optimizer.Optimizer.optimize(dag,
                                         blocked_resources=[failed],
                                         quiet=True)
    else:
        optimizer.Optimizer.optimize(dag,
                                     blocked_resources=[failed],
                                     quiet=True)
        assert task.best_resources is not None
        assert task.best_resources.region != failed.region
    assert lookup.call_count >= 2


@pytest.mark.parametrize('override,expected', [
    ({}, True),
    ({
        'instance_type': '1x_H200-SXM_SECURE--32vcpu-207gb-5.25usd'
    }, False),
    ({
        'instance_type': '1x_H200-SXM_SECURE--16vcpu-251gb-5.25usd'
    }, False),
    ({
        'instance_type': '1x_H100-SXM_SECURE--16vcpu-207gb-5.25usd'
    }, False),
    ({
        'instance_type': '1x_H200-SXM_SECURE--16vcpu-207gb-nanusd'
    }, False),
    ({
        'instance_type': '1x_H200-SXM_SECURE--16vcpu-207gb'
    }, False),
    ({
        'region': 'EU'
    }, False),
    ({
        'zone': 'US-TEST-2'
    }, False),
    ({
        'use_spot': True
    }, False),
    ({
        'cloud': clouds.AWS()
    }, False),
])
def test_priced_identity_reuse_and_blocking_preserve_shape(
        monkeypatch, offline, override, expected):
    _singleton(offline)
    lookup = mock.Mock(
        side_effect=AssertionError('Comparison queried provider'))
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    selected = Resources(
        cloud=clouds.RunPod(),
        instance_type='1x_H200-SXM_SECURE--16vcpu-207gb-4.59usd',
        region='US',
        zone='US-TEST-1',
        use_spot=False,
        accelerators={'H200-SXM': 1})
    requoted = selected.copy(**({
        'instance_type': '1x_H200-SXM_SECURE--16vcpu-207gb-5.25usd'
    } | override))
    assert selected.should_be_blocked_by(requoted) is expected
    assert selected.less_demanding_than(requoted) is expected
    assert requoted.less_demanding_than(selected) is expected
    lookup.assert_not_called()


def test_priced_blocking_retains_cloud_wildcard_and_literal_unknown_cloud(
        monkeypatch, offline):
    _singleton(offline)
    lookup = mock.Mock(
        side_effect=AssertionError('Comparison queried provider'))
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    selected = Resources(
        cloud=clouds.RunPod(),
        instance_type='1x_H200-SXM_SECURE--16vcpu-207gb-4.59usd')
    wildcard = Resources(
        instance_type='1x_H200-SXM_SECURE--16vcpu-207gb-5.25usd')
    assert selected.should_be_blocked_by(wildcard)
    assert wildcard.less_demanding_than(selected)
    assert not wildcard.less_demanding_than(selected.copy(cloud=None),
                                            check_cloud=False)
    assert selected.should_be_blocked_by(Resources(cloud=clouds.RunPod()))
    assert not Resources(cloud=clouds.RunPod()).should_be_blocked_by(selected)
    lookup.assert_not_called()


def test_admission_never_falls_back_to_a_replaced_selection_wrapper(
        monkeypatch, offline):
    _singleton(offline)
    fresh = mock.Mock(return_value=_host_quote())
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', fresh)
    request = Resources(cloud=clouds.RunPod(),
                        accelerators='H200-SXM:1',
                        cpus='16+',
                        memory='192+',
                        region='US')
    selected, = request.cloud._get_feasible_launchable_resources(
        request).resources_list
    cached = mock.Mock(return_value=_host_quote())
    monkeypatch.setattr(adaptor, 'get_gpu_host_quote', cached)
    fresh.return_value = None
    with pytest.raises(exceptions.ResourcesUnavailableError):
        selected.cloud.make_deploy_resources_variables(
            selected, resources_utils.ClusterName('offline', 'offline'),
            clouds.Region('US'), [clouds.Zone('US-TEST-1')], 1)
    cached.assert_not_called()
    assert fresh.call_count == 2


def test_sized_spot_render_refusal_uses_capacity_exception(
        monkeypatch, offline):
    _singleton(offline)
    lookup = mock.Mock(side_effect=AssertionError('Spot quote is unsupported'))
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    spot = Resources(cloud=clouds.RunPod(),
                     instance_type='1x_H200-SXM_SECURE--16vcpu-207gb-4.59usd',
                     region='US',
                     use_spot=True)
    assert not spot.cloud._get_feasible_launchable_resources(
        spot).resources_list
    with pytest.raises(ValueError, match='do not support spot'):
        spot.get_cost(3600)
    with pytest.raises(exceptions.ResourcesUnavailableError):
        spot.cloud.make_deploy_resources_variables(
            spot, resources_utils.ClusterName('offline', 'offline'),
            clouds.Region('US'), [clouds.Zone('US-TEST-1')], 1)
    lookup.assert_not_called()


def test_editable_estimate_does_not_authorize_price_over_cap(
        monkeypatch, offline):
    _singleton(offline)
    lookup = mock.Mock(return_value=_host_quote())
    monkeypatch.setattr(adaptor, '_get_gpu_host_quote', lookup)
    edited = Resources(
        cloud=clouds.RunPod(),
        instance_type='1x_H200-SXM_SECURE--16vcpu-207gb-0.0001usd',
        region='US',
        max_hourly_cost=4.0)
    edited.validate()
    assert edited.get_cost(
        3600) == 0.0001  # Editable estimate, not billing truth.
    assert not edited.cloud._get_feasible_launchable_resources(
        edited).resources_list
    with pytest.raises(exceptions.ResourcesUnavailableError):
        edited.cloud.make_deploy_resources_variables(
            edited, resources_utils.ClusterName('offline', 'offline'),
            clouds.Region('US'), [clouds.Zone('US-TEST-1')], 1)
    assert lookup.call_count == 2
