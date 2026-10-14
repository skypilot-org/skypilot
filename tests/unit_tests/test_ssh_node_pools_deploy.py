"""Unit tests for sky/ssh_node_pools/deploy/deploy.py helpers."""

import os
import subprocess

from sky.ssh_node_pools.deploy import deploy
from sky.ssh_node_pools.deploy import utils as deploy_utils


def test_prometheus_install_cmd_contains_required_fields():
    askpass_block = 'echo "askpass"'
    cmd = deploy._prometheus_install_cmd(askpass_block)

    # Must include the askpass block verbatim (consistent with sibling helpers).
    assert askpass_block in cmd

    # Must self-install helm if missing — the gpu-operator path installs
    # helm for GPU pools, but CPU-only pools skip that step.
    assert 'command -v helm' in cmd
    assert 'get-helm-3' in cmd

    # Must use the prometheus-community repo and the plain prometheus chart
    # (NOT kube-prometheus-stack — see spec "Do NOT use kube-prometheus-stack").
    assert 'prometheus-community' in cmd
    assert 'prometheus-community/prometheus' in cmd
    assert 'kube-prometheus-stack' not in cmd

    # Repo-scoped update is cheaper than a global `helm repo update`.
    assert 'helm repo update prometheus-community' in cmd

    # Must be idempotent (upgrade --install).
    assert 'helm upgrade --install' in cmd

    # Must target the correct kubeconfig on the remote head node.
    assert '--kubeconfig ~/.kube/config' in cmd
    assert '--namespace skypilot' in cmd
    assert '--create-namespace' in cmd

    # Release name hardcoded.
    assert 'skypilot-prometheus' in cmd

    # Must NOT pass --kube-context. The command runs on the pool's head node,
    # where `~/.kube/config` only has the default context k3s wrote — any
    # `ssh-<pool>` context name only exists in the client's merged kubeconfig.
    assert '--kube-context' not in cmd

    # Values file must be created via mktemp so concurrent pool deploys don't
    # race on a shared path.
    assert 'mktemp' in cmd

    # Helm exit code must be explicitly captured and re-raised. The rm-after-
    # helm pattern would otherwise mask a helm failure with a clean exit 0.
    assert 'HELM_RET=$?' in cmd
    assert 'exit $HELM_RET' in cmd

    # Must enable node-exporter (the deliberate deviation from the skill example).
    assert 'prometheus-node-exporter' in cmd

    # pushgateway and alertmanager explicitly disabled.
    assert 'prometheus-pushgateway' in cmd
    assert 'alertmanager' in cmd


def test_prometheus_install_cmd_node_exporter_enabled_not_disabled():
    """Regression: guard against ever flipping node-exporter to disabled."""
    cmd = deploy._prometheus_install_cmd('')
    # Find the prometheus-node-exporter section and verify it's enabled: true,
    # not enabled: false.
    ne_section = cmd[cmd.index('prometheus-node-exporter'):]
    # The first 'enabled:' after the node-exporter key must be 'true'.
    enabled_line = ne_section[ne_section.index('enabled:'):].splitlines()[0]
    assert enabled_line.strip() == 'enabled: true'


def test_askpass_script_returns_the_exact_password():
    """Passwords with shell characters must reach sudo unchanged."""
    password = 'p$w`d\'"\\ x;EOF'
    block = deploy.create_askpass_script(password)
    out = subprocess.run(['bash', '-c', block + '\n"$SUDO_ASKPASS"'],
                         capture_output=True,
                         text=True,
                         check=True)
    assert out.stdout == password + '\n'


def test_askpass_script_is_empty_without_password():
    assert deploy.create_askpass_script('') == ''


def test_tcp_forwarding_cmd_uses_askpass_for_every_sudo():
    askpass_block = 'export SUDO_ASKPASS=/tmp/askpass'
    cmd = deploy._tcp_forwarding_cmd(askpass_block, '10.0.0.1')
    assert cmd.startswith(askpass_block)
    # Every sudo must go through askpass; a plain `sudo` fails without a
    # terminal on hosts that need a sudo password.
    assert cmd.count('sudo ') == cmd.count('sudo -A ')
    assert cmd.count('sudo -A ') == 3
    assert '10.0.0.1' in cmd


def test_get_helm_runs_as_root_in_every_install_cmd():
    """get-helm-3's own plain `sudo` cannot use askpass without a terminal."""
    for cmd in (deploy._gpu_operator_install_cmd(''),
                deploy._prometheus_install_cmd('')):
        assert cmd.count('./get_helm.sh') == 1
        assert cmd.count(deploy._GET_HELM) == 1
        assert deploy._GET_HELM.startswith('sudo -A ')


def test_gpu_operator_cmd_is_rerunnable_and_drops_legacy_service():
    cmd = deploy._gpu_operator_install_cmd('')
    assert 'helm upgrade --install gpu-operator' in cmd
    assert 'helm install gpu-operator' not in cmd
    assert cmd.rstrip().endswith(deploy._DELETE_LEGACY_DCGM_SERVICE)


def _fake_run(calls, returncode):

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, returncode, 'out', 'err')

    return run


def test_run_remote_sends_script_on_stdin_not_argv(monkeypatch, tmp_path):
    password = 'S3cret$pw'
    key = tmp_path / 'key'
    key.write_text('k')
    cmd = deploy.create_askpass_script(password) + 'sudo -A true'
    calls = []
    monkeypatch.setattr(deploy_utils.subprocess, 'run', _fake_run(calls, 0))
    for use_ssh_config in (False, True):
        assert deploy_utils.run_remote('10.0.0.1',
                                       cmd,
                                       'ubuntu',
                                       str(key),
                                       use_ssh_config=use_ssh_config) == 'out'
    for args, kwargs in calls:
        assert not any(password in a for a in args)
        assert kwargs['input'] == cmd


def test_run_remote_failure_log_redacts_password(monkeypatch):
    password = 'S3cret$pw'
    cmd = deploy.create_askpass_script(password) + 'sudo -A false'
    errors = []
    monkeypatch.setattr(deploy_utils.subprocess, 'run', _fake_run([], 1))
    monkeypatch.setattr(deploy_utils.logger, 'error', errors.append)
    assert deploy_utils.run_remote('10.0.0.1', cmd) is None
    assert len(errors) == 1
    assert password not in errors[0]
    assert '<redacted>' in errors[0] and 'sudo -A false' in errors[0]


def test_remote_runner_runs_script_and_keeps_exit_code(tmp_path):
    """The script runs from a deleted temp file and cannot read itself."""
    out = subprocess.run(['bash', '-c', deploy_utils._REMOTE_RUNNER],
                         input='echo hi; cat; exit 7\n',
                         capture_output=True,
                         text=True,
                         env={
                             **os.environ, 'TMPDIR': str(tmp_path)
                         },
                         check=False)
    assert (out.returncode, out.stdout) == (7, 'hi\n')
    assert not list(tmp_path.iterdir())
