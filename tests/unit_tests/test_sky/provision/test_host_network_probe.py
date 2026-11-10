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
import gzip
import re
import socket

import pytest

from sky.provision import instance_setup
from sky.provision.kubernetes import host_network_probe
from sky.skylet import constants


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
        assert ('${SKYPILOT_RAY_NODE_IP:+'
                '--node-ip-address=$SKYPILOT_RAY_NODE_IP}') in cmd
        assert ('${SKYPILOT_RAY_NODE_MANAGER_PORT:+'
                '--node-manager-port=$SKYPILOT_RAY_NODE_MANAGER_PORT}') in cmd
        assert (
            '${SKYPILOT_RAY_CLIENT_SERVER_PORT:+'
            '--ray-client-server-port=$SKYPILOT_RAY_CLIENT_SERVER_PORT}') in cmd

    def test_head_prepended_probe_is_runtime_gated(self):
        cmd = instance_setup.ray_head_start_command(custom_resource=None,
                                                    custom_ray_options=None)
        # No-op shell branch unless hostNetwork is on. Gated on that alone:
        # the sshd_config rewrite lives inside the same `fi`, so a second
        # condition that stopped being set would silently skip moving sshd
        # off port 22.
        assert 'if [ "${SKYPILOT_HOST_NETWORK:-0}" = "1" ]' in cmd
        assert 'SKYPILOT_RAY_PORTS_CONFIGMAP_NAME' not in cmd
        assert (f'| base64 -d | gunzip > '
                f'{instance_setup._HOST_NETWORK_PROBE_TARGET}') in cmd
        assert '--mode head' in cmd

    def test_worker_prepended_probe_uses_worker_mode(self):
        cmd = instance_setup.ray_worker_start_command(custom_resource=None,
                                                      custom_ray_options=None,
                                                      no_restart=False)
        assert 'if [ "${SKYPILOT_HOST_NETWORK:-0}" = "1" ]' in cmd
        assert (f'| base64 -d | gunzip > '
                f'{instance_setup._HOST_NETWORK_PROBE_TARGET}') in cmd
        assert '--mode worker' in cmd

    def test_no_restart_worker_probes_only_when_it_starts_ray(self):
        """The probe must sit inside the already-running guard, not before it.

        The probe asserts the assigned ports are free *before ray binds
        them*. With no_restart, ray may already be up from an earlier
        attempt and holding exactly those ports -- so a probe outside the
        guard fails against our own raylet, and the retry can never
        succeed. Observed on the recovery path: the worker rejoined and
        the cluster was healthy, but `sky launch` reported failure.

        Asserted structurally rather than by substring order: the probe
        has to be inside the `|| { ... }` that also holds `ray start`,
        which is the property, whatever the command's spelling.
        """
        cmd = instance_setup.ray_worker_start_command(custom_resource=None,
                                                      custom_ray_options=None,
                                                      no_restart=True)
        guard, _, guarded = cmd.partition('|| {')
        assert guarded, 'expected the already-running guard for no_restart'
        assert '--mode worker' in guarded
        assert '--mode worker' not in guard

    def test_restarting_worker_still_probes_unconditionally(self):
        """Without no_restart the command runs `ray stop` first, so the
        ports really are free and the assertion is meaningful. Moving the
        probe for the guarded case must not disarm it here."""
        cmd = instance_setup.ray_worker_start_command(custom_resource=None,
                                                      custom_ray_options=None,
                                                      no_restart=False)
        assert '--mode worker' in cmd
        assert '|| {' not in cmd

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
        # The payload is minified + gzipped before b64'ing to keep the
        # rendered cluster YAML small. The bash counterpart pipes
        # `base64 -d | gunzip`.
        decoded = gzip.decompress(base64.b64decode(m.group(1))).decode('utf-8')
        # Minification must preserve identifiers and module structure
        # — the probe still needs --mode head and --mode worker entry
        # points and a parseable AST.
        assert 'def _run_head' in decoded
        assert 'def _run_worker' in decoded
        compile(decoded, '<probe>', 'exec')

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


class TestSshdPortPropagation:
    """sshd is probed by both head and worker; bootstrap snippet rebinds."""

    def test_sshd_is_in_both_head_and_worker_port_sets(self):
        # Each pod's sshd is in its own filesystem but shares the host
        # net namespace under hostNetwork, so each pod needs its own
        # probed port.
        assert 'sshd' in host_network_probe.HEAD_PORT_NAMES
        assert 'sshd' in host_network_probe.WORKER_PORT_NAMES

    def test_probe_cmd_rebinds_sshd_after_sourcing_env(self):
        cmd = instance_setup.ray_head_start_command(custom_resource=None,
                                                    custom_ray_options=None)
        # The Port-directive rewrite is gated on SKYPILOT_SSHD_PORT being
        # set, which the server writes into the pod env. Non-hostNetwork
        # bootstraps leave it unset and skip the rewrite.
        assert 'if [ -n "${SKYPILOT_SSHD_PORT:-}" ]' in cmd
        # Delete-then-append, all under one sudo, so the redirect
        # happens as root and we don't pay three sudo costs in a row.
        assert 'sudo sh -c "' in cmd
        assert ("sed -i -E '/^[[:space:]]*#?[[:space:]]*Port"
                "[[:space:]]+/d'") in cmd
        assert 'echo Port ${SKYPILOT_SSHD_PORT} >> /etc/ssh/sshd_config' in cmd
        assert 'service ssh restart' in cmd


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
