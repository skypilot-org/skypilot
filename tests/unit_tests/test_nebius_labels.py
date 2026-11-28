"""Tests that resources.labels reach Nebius instances and their boot disks."""
import importlib.util
import os
from unittest import mock

import jinja2
import pytest
import yaml

import sky
from sky.adaptors import nebius as nebius_adaptor
from sky.provision import common
from sky.provision.nebius import instance as nebius_instance
from sky.provision.nebius import utils as nebius_utils
from sky.utils import resources_utils

_HAS_NEBIUS_SDK = importlib.util.find_spec('nebius') is not None
nebius_sdk_required = pytest.mark.skipif(not _HAS_NEBIUS_SDK,
                                         reason='Nebius SDK is not installed')

_LABELS = {'team': 'ml-research', 'cost-center': 'cc-1234', 'env': 'dev'}


def _render_head_node_config(labels):
    templates = os.path.join(os.path.dirname(sky.__file__), 'templates')
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(templates),
                             undefined=jinja2.ChainableUndefined)
    rendered = env.get_template('nebius-ray.yml.j2').render(
        cluster_name_on_cloud='cluster',
        num_nodes=1,
        region='eu-north1',
        docker_image=None,
        ssh_proxy_command=None,
        instance_type='cpu-d3_4vcpu-16gb',
        image_id='ubuntu24.04-driverless',
        disk_size=100,
        filesystems=[],
        credentials={},
        labels=labels,
    )
    config = yaml.safe_load(rendered)
    return config['available_node_types']['ray_head_default']['node_config']


@pytest.mark.parametrize('labels', [_LABELS, {}])
def test_template_renders_labels_into_node_config(labels):
    assert _render_head_node_config(labels)['labels'] == labels


@pytest.mark.parametrize('labels', [_LABELS, {}])
def test_run_instances_passes_labels_to_launch(monkeypatch, labels):
    monkeypatch.setattr(nebius_instance, '_wait_until_no_pending',
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(nebius_instance, '_filter_instances',
                        lambda *args, **kwargs: {})
    launch = mock.Mock(return_value='computeinstance-id')
    monkeypatch.setattr(nebius_utils, 'launch', launch)

    nebius_instance.run_instances(
        region='eu-north1',
        cluster_name='cluster',
        cluster_name_on_cloud='cluster',
        config=common.ProvisionConfig(
            provider_config={
                'project_id': 'project-id',
                'use_internal_ips': False,
            },
            authentication_config={},
            docker_config={},
            node_config=_render_head_node_config(labels),
            count=1,
            tags={},
            resume_stopped_nodes=False,
            ports_to_open_on_launch=None,
        ),
    )

    assert launch.call_args.kwargs['labels'] == labels


def _launch_request(labels):
    """Build a Nebius launch request and return it."""
    compute = nebius_adaptor.compute()
    service = mock.MagicMock()
    instance = mock.MagicMock()
    instance.metadata.id = 'instance-id'
    instance.status.state.name = 'STARTING'

    with (
            mock.patch.object(nebius_utils,
                              'get_project_by_region',
                              return_value='project-id'),
            mock.patch.object(nebius_utils,
                              'get_subnet_id',
                              return_value='subnet-id'),
            mock.patch.object(nebius_adaptor, 'sdk'),
            mock.patch.object(compute,
                              'InstanceServiceClient',
                              return_value=service),
            mock.patch.object(nebius_adaptor,
                              'sync_call',
                              side_effect=[None, instance]),
    ):
        nebius_utils.launch(
            cluster_name_on_cloud='cluster',
            node_type='head',
            platform='cpu-d3',
            preset='4vcpu-16gb',
            region='eu-north1',
            image_id_or_family='ubuntu24.04-driverless',
            disk_size=100,
            user_data='',
            associate_public_ip_address=False,
            filesystems=[],
            disk_tier=resources_utils.DiskTier.MEDIUM,
            labels=labels,
        )

    return service.create.call_args.args[0]


@nebius_sdk_required
def test_launch_sets_labels_on_instance_and_boot_disk():
    request = _launch_request(_LABELS)

    assert dict(request.metadata.labels) == _LABELS
    assert dict(request.spec.boot_disk.managed_disk.labels) == _LABELS


@nebius_sdk_required
@pytest.mark.parametrize('labels', [None, {}])
def test_launch_without_labels_leaves_them_empty(labels):
    request = _launch_request(labels)

    assert not request.metadata.labels
    assert not request.spec.boot_disk.managed_disk.labels
