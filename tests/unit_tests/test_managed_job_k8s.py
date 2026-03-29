"""Unit tests for K8s managed job provisioning.

Tests the manifest builder, status tracking, entrypoint generation,
and template rendering without requiring a real K8s cluster.
"""
import os
import textwrap
from unittest import mock

import pytest

# Enable V1 managed jobs via config mock
from unittest import mock
mock.patch('sky.skypilot_config.get_nested',
           side_effect=lambda key, default=None: True
           if key == ('jobs', 'use_v1') else default).start()


class TestBuildJobManifest:
    """Tests for the IndexedJob manifest builder."""

    def test_single_node_no_service(self):
        from sky.provision.kubernetes.managed_job import build_job_manifest
        manifest, service, rbac = build_job_manifest(
            cluster_name_on_cloud='test-job',
            namespace='default',
            pod_spec={
                'metadata': {
                    'labels': {},
                    'annotations': {}
                },
                'spec': {
                    'containers': [{
                        'name': 'ray-node',
                        'image': 'ubuntu:22.04',
                        'resources': {
                            'requests': {
                                'cpu': '1'
                            }
                        },
                    }],
                },
            },
            num_nodes=1,
            setup_commands=None,
            run_commands='echo hello',
            envs={},
        )
        assert manifest['kind'] == 'Job'
        assert manifest['spec']['completions'] == 1
        assert manifest['spec']['parallelism'] == 1
        assert service is None  # No service for single node
        assert len(rbac) == 2  # Role + RoleBinding

    def test_multi_node_creates_service(self):
        from sky.provision.kubernetes.managed_job import build_job_manifest
        manifest, service, rbac = build_job_manifest(
            cluster_name_on_cloud='test-job',
            namespace='default',
            pod_spec={
                'metadata': {
                    'labels': {},
                    'annotations': {}
                },
                'spec': {
                    'containers': [{
                        'name': 'ray-node',
                        'image': 'ubuntu:22.04',
                        'resources': {
                            'requests': {
                                'cpu': '1'
                            }
                        },
                    }],
                },
            },
            num_nodes=4,
            setup_commands=None,
            run_commands='echo hello',
            envs={'MY_VAR': 'val'},
        )
        assert manifest['spec']['completions'] == 4
        assert manifest['spec']['parallelism'] == 4
        assert manifest['spec']['completionMode'] == 'Indexed'
        assert service is not None
        assert service['spec']['clusterIP'] == 'None'  # Headless

    def test_strips_init_containers(self):
        from sky.provision.kubernetes.managed_job import build_job_manifest
        manifest, _, _ = build_job_manifest(
            cluster_name_on_cloud='test',
            namespace='default',
            pod_spec={
                'metadata': {
                    'labels': {},
                    'annotations': {}
                },
                'spec': {
                    'containers': [{
                        'name': 'ray-node',
                        'image': 'ubuntu:22.04',
                    }],
                    'initContainers': [{
                        'name': 'setup',
                        'image': 'busybox',
                    }],
                },
            },
            num_nodes=1,
            setup_commands=None,
            run_commands='echo hi',
            envs={},
        )
        pod_spec = manifest['spec']['template']['spec']
        assert 'initContainers' not in pod_spec

    def test_strips_lifecycle_probes(self):
        from sky.provision.kubernetes.managed_job import build_job_manifest
        manifest, _, _ = build_job_manifest(
            cluster_name_on_cloud='test',
            namespace='default',
            pod_spec={
                'metadata': {
                    'labels': {},
                    'annotations': {}
                },
                'spec': {
                    'containers': [{
                        'name': 'ray-node',
                        'image': 'ubuntu:22.04',
                        'lifecycle': {
                            'postStart': {}
                        },
                        'readinessProbe': {
                            'exec': {}
                        },
                        'livenessProbe': {
                            'exec': {}
                        },
                    }],
                },
            },
            num_nodes=1,
            setup_commands=None,
            run_commands='echo hi',
            envs={},
        )
        container = manifest['spec']['template']['spec']['containers'][0]
        assert 'lifecycle' not in container
        assert 'readinessProbe' not in container
        assert 'livenessProbe' not in container

    def test_only_one_container_kept(self):
        """Extra sidecar containers from Ray spec should be stripped."""
        from sky.provision.kubernetes.managed_job import build_job_manifest
        manifest, _, _ = build_job_manifest(
            cluster_name_on_cloud='test',
            namespace='default',
            pod_spec={
                'metadata': {
                    'labels': {},
                    'annotations': {}
                },
                'spec': {
                    'containers': [
                        {
                            'name': 'ray-node',
                            'image': 'ubuntu:22.04'
                        },
                        {
                            'name': 'sidecar',
                            'image': 'busybox'
                        },
                    ],
                },
            },
            num_nodes=1,
            setup_commands=None,
            run_commands='echo hi',
            envs={},
        )
        containers = manifest['spec']['template']['spec']['containers']
        assert len(containers) == 1
        assert containers[0]['command'] == ['/bin/bash', '-c']

    def test_restart_policy_never(self):
        from sky.provision.kubernetes.managed_job import build_job_manifest
        manifest, _, _ = build_job_manifest(
            cluster_name_on_cloud='test',
            namespace='default',
            pod_spec={
                'metadata': {
                    'labels': {},
                    'annotations': {}
                },
                'spec': {
                    'containers': [{
                        'name': 'main',
                        'image': 'ubuntu:22.04'
                    },],
                },
            },
            num_nodes=1,
            setup_commands=None,
            run_commands='echo hi',
            envs={},
        )
        assert manifest['spec']['template']['spec']['restartPolicy'] == 'Never'

    def test_rbac_has_pods_patch(self):
        """RBAC should include pods patch for phase/app_status labels."""
        from sky.provision.kubernetes.managed_job import build_job_manifest
        _, _, rbac = build_job_manifest(
            cluster_name_on_cloud='test',
            namespace='default',
            pod_spec={
                'metadata': {
                    'labels': {},
                    'annotations': {}
                },
                'spec': {
                    'containers': [{
                        'name': 'main',
                        'image': 'ubuntu:22.04'
                    },],
                },
            },
            num_nodes=1,
            setup_commands=None,
            run_commands='echo hi',
            envs={},
        )
        role = [r for r in rbac if r['kind'] == 'Role'][0]
        rules = role['rules']
        pod_rule = [r for r in rules if 'pods' in r['resources']][0]
        assert 'patch' in pod_rule['verbs']
        assert 'get' in pod_rule['verbs']
        assert 'list' in pod_rule['verbs']


class TestEntrypointScript:
    """Tests for the entrypoint script generation."""

    def test_single_node_no_peer_discovery(self):
        from sky.provision.kubernetes.managed_job import (
            _build_entrypoint_script)
        script = _build_entrypoint_script('test', 'default', 1, None,
                                          'echo hello', 0)
        assert 'SKYPILOT_NODE_RANK' in script
        assert 'echo hello' in script
        # Single node should NOT have node count waiting
        assert 'Waiting for' not in script

    def test_multi_node_has_node_discovery(self):
        from sky.provision.kubernetes.managed_job import (
            _build_entrypoint_script)
        script = _build_entrypoint_script('test', 'default', 4, None,
                                          'echo hello', 0)
        assert 'Waiting for 4 initialized nodes' in script
        assert 'skypilot_nodes' in script

    def test_setup_commands_included(self):
        from sky.provision.kubernetes.managed_job import (
            _build_entrypoint_script)
        script = _build_entrypoint_script('test', 'default', 1,
                                          'pip install torch',
                                          'python train.py', 2)
        assert 'pip install torch' in script
        assert 'python train.py' in script

    def test_has_phase_labels(self):
        """Entrypoint should patch pod labels at phase transitions."""
        from sky.provision.kubernetes.managed_job import (
            _build_entrypoint_script)
        script = _build_entrypoint_script('test', 'default', 1, 'echo setup',
                                          'echo run', 0)
        assert '_skypilot_set_phase PENDING' in script
        assert '_skypilot_set_phase SETTING_UP' in script
        assert '_skypilot_set_phase RUNNING' in script

    def test_has_discovery_server(self):
        """Entrypoint should start background discovery server."""
        from sky.provision.kubernetes.managed_job import (
            _build_entrypoint_script)
        script = _build_entrypoint_script('test', 'default', 1, None, 'echo hi',
                                          0)
        assert 'discovery server' in script.lower()
        assert 'DISCOVERY_PID' in script

    def test_has_app_status_env(self):
        """Entrypoint should export SKYPILOT_APP_STATUS."""
        from sky.provision.kubernetes.managed_job import (
            _build_entrypoint_script)
        script = _build_entrypoint_script('test', 'default', 1, None, 'echo hi',
                                          0)
        assert 'SKYPILOT_APP_STATUS' in script

    def test_skypilot_nodes_helper(self):
        """Entrypoint should define skypilot_nodes() helper."""
        from sky.provision.kubernetes.managed_job import (
            _build_entrypoint_script)
        script = _build_entrypoint_script('test', 'default', 1, None, 'echo hi',
                                          0)
        assert 'skypilot_nodes()' in script


class TestMainContainerState:
    """Tests for _get_main_container_state."""

    def test_running_state(self):
        from sky.provision.kubernetes.managed_job import (
            _get_main_container_state)
        pod = mock.MagicMock()
        cs = mock.MagicMock()
        cs.name = 'main'
        cs.state.terminated = None
        cs.state.running = mock.MagicMock()
        cs.state.waiting = None
        pod.status.container_statuses = [cs]
        assert _get_main_container_state(pod) == 'running'

    def test_succeeded_state(self):
        from sky.provision.kubernetes.managed_job import (
            _get_main_container_state)
        pod = mock.MagicMock()
        cs = mock.MagicMock()
        cs.name = 'main'
        cs.state.terminated = mock.MagicMock(exit_code=0)
        cs.state.running = None
        cs.state.waiting = None
        pod.status.container_statuses = [cs]
        assert _get_main_container_state(pod) == 'succeeded'

    def test_failed_state(self):
        from sky.provision.kubernetes.managed_job import (
            _get_main_container_state)
        pod = mock.MagicMock()
        cs = mock.MagicMock()
        cs.name = 'main'
        cs.state.terminated = mock.MagicMock(exit_code=1)
        cs.state.running = None
        cs.state.waiting = None
        pod.status.container_statuses = [cs]
        assert _get_main_container_state(pod) == 'failed'

    def test_no_container_statuses(self):
        from sky.provision.kubernetes.managed_job import (
            _get_main_container_state)
        pod = mock.MagicMock()
        pod.status.container_statuses = None
        assert _get_main_container_state(pod) == 'pending'


class TestElasticJobStatus:
    """Tests for get_elastic_job_status."""

    def _make_pod(self, main_state='running', name='pod-0', index='0'):
        pod = mock.MagicMock()
        pod.metadata.name = name
        pod.metadata.labels = {'skypilot-node-index': index}
        cs = mock.MagicMock()
        cs.name = 'main'
        if main_state == 'running':
            cs.state.terminated = None
            cs.state.running = mock.MagicMock()
            cs.state.waiting = None
        elif main_state == 'succeeded':
            cs.state.terminated = mock.MagicMock(exit_code=0)
            cs.state.running = None
            cs.state.waiting = None
        elif main_state == 'failed':
            cs.state.terminated = mock.MagicMock(exit_code=1)
            cs.state.running = None
            cs.state.waiting = None
        elif main_state == 'pending':
            cs.state.terminated = None
            cs.state.running = None
            cs.state.waiting = mock.MagicMock()
        pod.status.container_statuses = [cs]
        pod.status.phase = 'Running'
        return pod

    @mock.patch('sky.adaptors.kubernetes.core_api')
    def test_all_running(self, mock_core_api):
        from sky.provision.kubernetes.managed_job import get_elastic_job_status
        from sky.provision.kubernetes.managed_job import ManagedJobStatus
        pods_response = mock.MagicMock()
        pods_response.items = [
            self._make_pod('running', f'pod-{i}', str(i)) for i in range(4)
        ]
        mock_core_api.return_value.list_namespaced_pod.return_value = (
            pods_response)
        status, running, succeeded, failed = get_elastic_job_status('test',
                                                                    'default',
                                                                    None,
                                                                    min_nodes=2)
        assert status == ManagedJobStatus.RUNNING
        assert running == 4
        assert succeeded == 0
        assert failed == 0

    @mock.patch('sky.adaptors.kubernetes.core_api')
    def test_all_succeeded(self, mock_core_api):
        from sky.provision.kubernetes.managed_job import get_elastic_job_status
        from sky.provision.kubernetes.managed_job import ManagedJobStatus
        pods_response = mock.MagicMock()
        pods_response.items = [
            self._make_pod('succeeded', f'pod-{i}', str(i)) for i in range(4)
        ]
        mock_core_api.return_value.list_namespaced_pod.return_value = (
            pods_response)
        status, running, succeeded, failed = get_elastic_job_status('test',
                                                                    'default',
                                                                    None,
                                                                    min_nodes=2)
        assert status == ManagedJobStatus.SUCCEEDED
        assert succeeded == 4

    @mock.patch('sky.adaptors.kubernetes.core_api')
    def test_below_min_nodes(self, mock_core_api):
        from sky.provision.kubernetes.managed_job import get_elastic_job_status
        from sky.provision.kubernetes.managed_job import ManagedJobStatus
        pods_response = mock.MagicMock()
        pods_response.items = [
            self._make_pod('running', 'pod-0', '0'),
        ]
        mock_core_api.return_value.list_namespaced_pod.return_value = (
            pods_response)
        status, running, succeeded, failed = get_elastic_job_status('test',
                                                                    'default',
                                                                    None,
                                                                    min_nodes=2)
        assert status == ManagedJobStatus.SETTING_UP
        assert running == 1

    @mock.patch('sky.adaptors.kubernetes.core_api')
    def test_pending_not_counted_as_succeeded(self, mock_core_api):
        """Pending pods should prevent false SUCCEEDED (issue I6)."""
        from sky.provision.kubernetes.managed_job import get_elastic_job_status
        from sky.provision.kubernetes.managed_job import ManagedJobStatus
        pods_response = mock.MagicMock()
        pods_response.items = [
            self._make_pod('succeeded', 'pod-0', '0'),
            self._make_pod('succeeded', 'pod-1', '1'),
            self._make_pod('pending', 'pod-2', '2'),
        ]
        mock_core_api.return_value.list_namespaced_pod.return_value = (
            pods_response)
        status, running, succeeded, failed = get_elastic_job_status('test',
                                                                    'default',
                                                                    None,
                                                                    min_nodes=1)
        # Should NOT be SUCCEEDED because pod-2 is pending
        assert status != ManagedJobStatus.SUCCEEDED


class TestPodsToReplace:
    """Tests for get_pods_to_replace."""

    @mock.patch('sky.adaptors.kubernetes.core_api')
    def test_missing_pod_detected(self, mock_core_api):
        from sky.provision.kubernetes.managed_job import get_pods_to_replace
        pod0 = mock.MagicMock()
        pod0.metadata.labels = {'skypilot-node-index': '0'}
        pod0.status.phase = 'Running'
        pod0.status.container_statuses = None
        # Pod 1 is missing entirely
        pods_response = mock.MagicMock()
        pods_response.items = [pod0]
        mock_core_api.return_value.list_namespaced_pod.return_value = (
            pods_response)

        # Expect index 1 (missing) and 2 (missing) to be replaced
        to_replace = get_pods_to_replace('test', 'default', None, num_nodes=3)
        assert 1 in to_replace
        assert 2 in to_replace
        assert 0 not in to_replace  # Pod 0 exists

    @mock.patch('sky.adaptors.kubernetes.core_api')
    def test_succeeded_pod_not_replaced(self, mock_core_api):
        from sky.provision.kubernetes.managed_job import (
            _get_main_container_state)
        from sky.provision.kubernetes.managed_job import get_pods_to_replace
        pod0 = mock.MagicMock()
        pod0.metadata.labels = {'skypilot-node-index': '0'}
        cs = mock.MagicMock()
        cs.name = 'main'
        cs.state.terminated = mock.MagicMock(exit_code=0)
        cs.state.running = None
        cs.state.waiting = None
        pod0.status.container_statuses = [cs]
        pods_response = mock.MagicMock()
        pods_response.items = [pod0]
        mock_core_api.return_value.list_namespaced_pod.return_value = (
            pods_response)
        to_replace = get_pods_to_replace('test', 'default', None, num_nodes=1)
        assert 0 not in to_replace  # Succeeded, don't replace


class TestElasticPodTemplate:
    """Tests for the Jinja2 elastic pod template."""

    def test_template_renders(self):
        from sky.provision.kubernetes.managed_job import render_elastic_pod
        pod = render_elastic_pod(
            job_name='test',
            namespace='default',
            index=0,
            num_nodes=2,
            min_nodes=1,
            image='python:3.11-slim',
            setup_commands='echo setup',
            run_commands='echo hello',
            envs={'FOO': 'bar'},
        )
        assert pod['kind'] == 'Pod'
        assert pod['metadata']['name'] == 'test-0'
        assert pod['spec']['hostname'] == 'test-0'
        assert pod['spec']['subdomain'] == 'test'

    def test_template_has_single_container(self):
        """Template should have only main container (no sidecar)."""
        from sky.provision.kubernetes.managed_job import render_elastic_pod
        pod = render_elastic_pod(
            job_name='test',
            namespace='default',
            index=0,
            num_nodes=1,
            min_nodes=1,
            image='python:3.11-slim',
            setup_commands=None,
            run_commands='echo hi',
            envs={},
        )
        containers = pod['spec']['containers']
        assert len(containers) == 1
        assert containers[0]['name'] == 'main'

    def test_collocate_adds_affinity(self):
        from sky.provision.kubernetes.managed_job import render_elastic_pod
        pod = render_elastic_pod(
            job_name='test',
            namespace='default',
            index=0,
            num_nodes=2,
            min_nodes=1,
            image='python:3.11-slim',
            setup_commands=None,
            run_commands='echo hi',
            envs={},
            collocate=True,
        )
        affinity = pod['spec'].get('affinity', {})
        assert 'podAffinity' in affinity

    def test_skypilot_nodes_function_in_entrypoint(self):
        from sky.provision.kubernetes.managed_job import render_elastic_pod
        pod = render_elastic_pod(
            job_name='test',
            namespace='default',
            index=0,
            num_nodes=1,
            min_nodes=1,
            image='python:3.11-slim',
            setup_commands=None,
            run_commands='echo hi',
            envs={},
        )
        main = pod['spec']['containers'][0]
        entrypoint = main['args'][0]
        assert 'skypilot_nodes' in entrypoint

    def test_phase_labels_in_entrypoint(self):
        from sky.provision.kubernetes.managed_job import render_elastic_pod
        pod = render_elastic_pod(
            job_name='test',
            namespace='default',
            index=0,
            num_nodes=1,
            min_nodes=1,
            image='python:3.11-slim',
            setup_commands='echo setup',
            run_commands='echo run',
            envs={},
        )
        main = pod['spec']['containers'][0]
        entrypoint = main['args'][0]
        assert '_skypilot_set_phase' in entrypoint
        assert 'SETTING_UP' in entrypoint
        assert 'RUNNING' in entrypoint

    def test_app_status_env_var(self):
        from sky.provision.kubernetes.managed_job import render_elastic_pod
        pod = render_elastic_pod(
            job_name='test',
            namespace='default',
            index=0,
            num_nodes=1,
            min_nodes=1,
            image='python:3.11-slim',
            setup_commands=None,
            run_commands='echo hi',
            envs={},
        )
        main = pod['spec']['containers'][0]
        env_names = [e['name'] for e in main.get('env', [])]
        assert 'SKYPILOT_APP_STATUS' in env_names

    def test_jobgroup_tasks_env_var(self):
        from sky.provision.kubernetes.managed_job import render_elastic_pod
        pod = render_elastic_pod(
            job_name='test',
            namespace='default',
            index=0,
            num_nodes=1,
            min_nodes=1,
            image='python:3.11-slim',
            setup_commands=None,
            run_commands='echo hi',
            envs={},
            jobgroup_tasks='ctrl:svc1,workers:svc2',
        )
        main = pod['spec']['containers'][0]
        env_map = {e['name']: e['value'] for e in main.get('env', [])}
        assert env_map.get(
            'SKYPILOT_JOBGROUP_TASKS') == 'ctrl:svc1,workers:svc2'

    def test_gang_barrier_uses_nodes_endpoint(self):
        from sky.provision.kubernetes.managed_job import render_elastic_pod
        pod = render_elastic_pod(
            job_name='test',
            namespace='default',
            index=0,
            num_nodes=4,
            min_nodes=2,
            image='python:3.11-slim',
            setup_commands=None,
            run_commands='echo hi',
            envs={},
        )
        main = pod['spec']['containers'][0]
        entrypoint = main['args'][0]
        # Gang barrier should use /nodes, not /peers
        assert '/nodes' in entrypoint
        assert 'min_nodes' not in entrypoint or 'min=' in entrypoint


class TestDiscoveryServer:
    """Tests for the discovery server script."""

    def test_script_is_valid_python(self):
        import ast

        from sky.provision.kubernetes.discovery_sidecar import (
            DISCOVERY_SERVER_SCRIPT)

        # Should parse without errors
        ast.parse(DISCOVERY_SERVER_SCRIPT)

    def test_port_constant(self):
        from sky.provision.kubernetes.discovery_sidecar import (
            DISCOVERY_SERVER_PORT)
        assert DISCOVERY_SERVER_PORT == 9876

    def test_script_has_nodes_handler(self):
        from sky.provision.kubernetes.discovery_sidecar import (
            DISCOVERY_SERVER_SCRIPT)
        assert '/nodes' in DISCOVERY_SERVER_SCRIPT
        assert 'GET' in DISCOVERY_SERVER_SCRIPT

    def test_script_has_app_status_watcher(self):
        from sky.provision.kubernetes.discovery_sidecar import (
            DISCOVERY_SERVER_SCRIPT)
        assert 'app_status_watcher' in DISCOVERY_SERVER_SCRIPT
        assert 'SKYPILOT_APP_STATUS' in DISCOVERY_SERVER_SCRIPT

    def test_script_has_proxy_logic(self):
        """Non-head pods should proxy /nodes to rank-0."""
        from sky.provision.kubernetes.discovery_sidecar import (
            DISCOVERY_SERVER_SCRIPT)
        assert 'proxy' in DISCOVERY_SERVER_SCRIPT.lower()
        assert 'IS_HEAD' in DISCOVERY_SERVER_SCRIPT

    def test_no_old_constants(self):
        """Old constants should be removed."""
        import sky.provision.kubernetes.discovery_sidecar as ds
        assert not hasattr(ds, 'DISCOVERY_SIDECAR_SCRIPT')
        assert not hasattr(ds, 'DISCOVERY_SIDECAR_PORT')
        assert not hasattr(ds, 'CONTROLLER_DISCOVERY_SCRIPT')
        assert not hasattr(ds, 'CONTROLLER_DISCOVERY_PORT')
