"""Unit tests for hostNetwork-aware Ray port handling.

Covers:
* sky.provision.kubernetes.host_network_probe — the port probe + env
  file emission used inside hostNetwork: true K8s pods.
* sky.provision.instance_setup — the env-var-substituted port flags and
  the probe-gating snippet in ray_head_start_command /
  ray_worker_start_command.

The probe module's ConfigMap publish/poll paths require a live K8s API
and are exercised by smoke tests instead.
"""
import base64
import re
import socket

from sky.provision import instance_setup
from sky.provision.kubernetes import host_network_probe
from sky.skylet import constants


class TestProbePorts:
    """The bind-and-hold port probe should produce N distinct usable ports."""

    def test_probe_head_returns_unique_ports(self):
        held, ports = host_network_probe._probe_ports(
            host_network_probe._HEAD_PORT_NAMES)
        try:
            assert set(ports.keys()) == set(host_network_probe._HEAD_PORT_NAMES)
            assert len(set(ports.values())) == len(ports)
            for port in ports.values():
                assert 1024 <= port <= 65535
        finally:
            for sock in held:
                sock.close()

    def test_probe_worker_subset_of_head_names(self):
        # Workers don't run GCS/dashboard/ray-client-server.
        for name in host_network_probe._WORKER_PORT_NAMES:
            assert name in host_network_probe._HEAD_PORT_NAMES

    def test_write_env_file_uses_skypilot_ray_env_var_names(self, tmp_path):
        path = tmp_path / 'env.sh'
        host_network_probe._write_env_file(
            {
                'gcs': 6380,
                'dashboard': 8266,
                'node_manager': 50001,
            }, str(path))
        body = path.read_text()
        # SKYPILOT_RAY_PORT (not _GCS_PORT) is the existing public name
        # for the head's GCS port; the worker template already exports
        # it under that name when hostNetwork is off.
        assert 'export SKYPILOT_RAY_PORT=6380' in body
        assert 'export SKYPILOT_RAY_DASHBOARD_PORT=8266' in body
        assert 'export SKYPILOT_RAY_NODE_MANAGER_PORT=50001' in body


class TestRayStartCommands:
    """ray_head_start_command / ray_worker_start_command behavior."""

    def test_head_uses_default_ports_when_env_vars_unset(self):
        cmd = instance_setup.ray_head_start_command(custom_resource=None,
                                                    custom_ray_options=None)
        # The substitution ${VAR:-default} expands to the constant when
        # SKYPILOT_HOST_NETWORK is unset; we check the literal expansion
        # text is present in the emitted shell command.
        assert (f'--port=${{SKYPILOT_RAY_PORT:-'
                f'{constants.SKY_REMOTE_RAY_PORT}}}') in cmd
        assert (f'--dashboard-port=${{SKYPILOT_RAY_DASHBOARD_PORT:-'
                f'{constants.SKY_REMOTE_RAY_DASHBOARD_PORT}}}') in cmd
        assert '--object-manager-port=${SKYPILOT_RAY_OBJECT_MANAGER_PORT:-8076}' in cmd

    def test_head_omits_new_port_flags_when_env_vars_unset(self):
        cmd = instance_setup.ray_head_start_command(custom_resource=None,
                                                    custom_ray_options=None)
        # ${VAR:+--flag=$VAR} expands to nothing when VAR is unset, so
        # Ray sees its own default behavior for these. We verify the
        # substitution form is in place (i.e., we didn't accidentally
        # hardcode a value).
        assert ('${SKYPILOT_RAY_NODE_MANAGER_PORT:+'
                '--node-manager-port=$SKYPILOT_RAY_NODE_MANAGER_PORT}') in cmd
        assert (
            '${SKYPILOT_RAY_CLIENT_SERVER_PORT:+'
            '--ray-client-server-port=$SKYPILOT_RAY_CLIENT_SERVER_PORT}') in cmd

    def test_head_prepended_probe_is_runtime_gated(self):
        cmd = instance_setup.ray_head_start_command(custom_resource=None,
                                                    custom_ray_options=None)
        # No-op shell branch unless both env vars are set on the pod.
        assert 'if [ "${SKYPILOT_HOST_NETWORK:-0}" = "1" ]' in cmd
        assert '[ -n "${SKYPILOT_RAY_PORTS_CONFIGMAP_NAME:-}" ]' in cmd
        assert (f'| base64 -d > {instance_setup._HOST_NETWORK_PROBE_TARGET}'
                in cmd)
        assert '--mode head' in cmd

    def test_worker_prepended_probe_uses_worker_mode(self):
        cmd = instance_setup.ray_worker_start_command(custom_resource=None,
                                                      custom_ray_options=None,
                                                      no_restart=False)
        assert 'if [ "${SKYPILOT_HOST_NETWORK:-0}" = "1" ]' in cmd
        assert (f'| base64 -d > {instance_setup._HOST_NETWORK_PROBE_TARGET}'
                in cmd)
        assert '--mode worker' in cmd

    def test_probe_command_has_no_unencoded_newlines(self):
        # Newlines in the bash command land at column 0 of the rendered
        # cluster YAML and break block-scalar parsing — the b64 payload
        # must stay on one line.
        cmd = instance_setup.ray_head_start_command(custom_resource=None,
                                                    custom_ray_options=None)
        m = re.search(r"echo '([A-Za-z0-9+/=]+)' \| base64 -d", cmd)
        assert m is not None, ('expected base64 payload not found '
                               'in probe command')
        assert '\n' not in m.group(1)

    def test_probe_script_round_trips_through_base64(self):
        cmd = instance_setup.ray_head_start_command(custom_resource=None,
                                                    custom_ray_options=None)
        m = re.search(r"echo '([A-Za-z0-9+/=]+)' \| base64 -d", cmd)
        assert m is not None
        decoded = base64.b64decode(m.group(1)).decode('utf-8')
        assert 'def _run_head' in decoded
        assert 'def _run_worker' in decoded

    def test_worker_keeps_existing_object_manager_default(self):
        cmd = instance_setup.ray_worker_start_command(custom_resource=None,
                                                      custom_ray_options=None,
                                                      no_restart=False)
        assert ('--object-manager-port=${SKYPILOT_RAY_OBJECT_MANAGER_PORT:-'
                '8076}') in cmd

    def test_custom_ray_options_user_overrides_appended_last(self):
        # The for-loop over custom_ray_options runs after the templated
        # flag string is built, so a user-supplied --port=7000 lands
        # after the ${SKYPILOT_RAY_PORT:-...} expansion. Ray honors the
        # last --port on the command line, so user overrides win.
        cmd = instance_setup.ray_head_start_command(
            custom_resource=None, custom_ray_options={'port': 7000})
        port_positions = [m.start() for m in re.finditer(r'--port=', cmd)]
        assert len(port_positions) == 2
        # User value comes after the env-var substitution.
        assert cmd.index('--port=7000') > cmd.index(
            '--port=${SKYPILOT_RAY_PORT')

    def test_dump_ray_ports_reads_env_vars(self):
        # The runtime-resolved dict expression in
        # SKY_REMOTE_RAY_PORT_DICT_STR must look up env vars so the
        # probed port set lands in ~/.sky/ray_port.json (and the file
        # falls back to the SkyPilot defaults when env vars are unset).
        assert 'os.environ.get("SKYPILOT_RAY_PORT"' in (
            constants.SKY_REMOTE_RAY_PORT_DICT_STR)
        assert 'os.environ.get("SKYPILOT_RAY_DASHBOARD_PORT"' in (
            constants.SKY_REMOTE_RAY_PORT_DICT_STR)


class TestConfigMapBody:
    """ConfigMap should carry an ownerReference so it is GC'd with the pod."""

    def test_body_pins_lifetime_to_head_pod(self):
        body = host_network_probe._build_configmap_body(
            name='cluster-xyz-ray-ports',
            namespace='my-ns',
            ports={
                'gcs': 6380,
                'dashboard': 8266
            },
            owner_pod_name='cluster-xyz-head-abc',
            owner_pod_uid='11111111-2222-3333-4444-555555555555',
        )
        # K8s deletes ConfigMaps whose owner Pod has been deleted, so on
        # `sky down` (which deletes the head pod) the ConfigMap is GC'd
        # without any extra teardown logic in SkyPilot.
        owner_refs = body['metadata']['ownerReferences']
        assert len(owner_refs) == 1
        assert owner_refs[0]['kind'] == 'Pod'
        assert owner_refs[0]['name'] == 'cluster-xyz-head-abc'
        assert owner_refs[0]['uid'] == ('11111111-2222-3333-4444-555555555555')
        # blockOwnerDeletion=false: deleting the head pod doesn't wait
        # for the ConfigMap. controller=false: we're not the pod's
        # controller, just a dependent.
        assert owner_refs[0]['controller'] is False
        assert owner_refs[0]['blockOwnerDeletion'] is False

    def test_body_carries_port_data_as_strings(self):
        body = host_network_probe._build_configmap_body(
            name='c-ray-ports',
            namespace='ns',
            ports={
                'gcs': 6380,
                'dashboard': 8266
            },
            owner_pod_name='c-head-xx',
            owner_pod_uid='uid-1',
        )
        # ConfigMap data values must be strings.
        assert body['data'] == {'gcs': '6380', 'dashboard': '8266'}


class TestWaitHeadGcsTcp:
    """The worker probe's TCP wait should accept once the head port is up."""

    def test_returns_when_port_is_listening(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        try:
            _, port = listener.getsockname()
            # Should return promptly without raising.
            host_network_probe._wait_head_gcs_tcp('127.0.0.1', port)
        finally:
            listener.close()
