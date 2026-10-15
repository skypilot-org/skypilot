"""Unit tests for sky/ssh_node_pools/deploy/deploy.py helpers."""

import json
import os
import re
import subprocess
import sys
import time
from unittest import mock

import pytest

from sky.ssh_node_pools import utils as ssh_utils
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


@pytest.mark.parametrize('per_host', [False, True])
def test_numeric_password_reaches_sudo_as_text(tmp_path, per_host):
    """YAML reads `password: 123456` as an int; it must still work."""
    key = tmp_path / 'key'
    key.write_text('k')
    config = {'user': 'u', 'identity_file': str(key)}
    if per_host:
        config['hosts'] = [{'ip': '10.0.0.1', 'password': 123456}]
    else:
        config['hosts'] = ['10.0.0.1']
        config['password'] = 123456
    (host,) = ssh_utils.prepare_hosts_info('p', config)
    block = deploy.create_askpass_script(host['password'])
    out = subprocess.run(['bash', '-c', block + '\n"$SUDO_ASKPASS"'],
                         capture_output=True,
                         text=True,
                         check=True)
    assert out.stdout == '123456\n'


@pytest.mark.parametrize('sep', ['\n', '\r'])
def test_askpass_script_rejects_line_breaks(sep):
    """A line break could close the heredoc and run the rest as commands."""
    with pytest.raises(ValueError, match='line break'):
        deploy.create_askpass_script(
            f'x{sep}{deploy_utils.ASKPASS_EOF}{sep}touch /tmp/pwned')


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
    # A host may already have an `nvidia` repo with another URL.
    assert 'nvidia --force-update &&' in cmd
    assert 'helm install gpu-operator' not in cmd
    assert deploy._DELETE_LEGACY_DCGM_SERVICE in cmd


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
        assert '-T' in args and args[-1] == deploy_utils._REMOTE_RUNNER
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


def _stub_bin(tmp_path, stubs):
    """A PATH dir whose executables are the given bash bodies."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for name, body in stubs.items():
        path = bin_dir / name
        path.write_text(f'#!/bin/bash\n{body}\n')
        path.chmod(0o755)
    return {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}'}


def test_remote_runner_runs_script_and_keeps_exit_code(tmp_path):
    out = subprocess.run(['bash', '-c', deploy_utils._REMOTE_RUNNER],
                         input='echo hi; exit 7\n',
                         capture_output=True,
                         text=True,
                         env={
                             **os.environ, 'TMPDIR': str(tmp_path)
                         },
                         check=False)
    assert (out.returncode, out.stdout) == (7, 'hi\n')
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(sys.platform != 'linux', reason='GNU mktemp honors TMPDIR')
def test_remote_runner_leaves_no_file_while_receiving(tmp_path):
    """The script file is unlinked before any of the script arrives."""
    proc = subprocess.Popen(['bash', '-c', deploy_utils._REMOTE_RUNNER],
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            text=True,
                            env={
                                **os.environ, 'TMPDIR': str(tmp_path)
                            })
    proc.stdin.write('echo secret\n')
    proc.stdin.flush()
    time.sleep(0.5)
    assert not list(tmp_path.iterdir())
    proc.stdin.write('exit 3\n')
    proc.stdin.close()
    assert proc.wait(timeout=10) == 3
    assert proc.stdout.read() == 'secret\n'


def test_run_remote_runs_tcp_forwarding_cmd_with_password(tmp_path):
    """End to end through a fake ssh: quoting and askpass survive the trip."""
    password = 'p$w\'d"x`;q\\'
    env = _stub_bin(
        tmp_path,
        {
            # ssh runs its last argument, like sshd's `$SHELL -c`.
            'ssh': 'exec bash -c "${@: -1}"',
            # sudo -A: check the askpass helper returns the password.
            'sudo': ('[ "$1" = -A ] && shift; '
                     '[ "$("$SUDO_ASKPASS")" = "$EXPECTED" ] || exit 1; '
                     '"$@"'),
            'sshd': 'echo "allowtcpforwarding yes"',
        })
    env['EXPECTED'] = password
    cmd = deploy._tcp_forwarding_cmd(deploy.create_askpass_script(password),
                                     '10.0.0.1')
    with mock.patch.dict(os.environ, env, clear=True):
        out = deploy_utils.run_remote('10.0.0.1', cmd, use_ssh_config=True)
    assert out == 'TCP Forwarding already enabled on head node (10.0.0.1).'


def _gpu_install(tmp_path, helm_upgrade_rc, kubectl_delete_rc):
    env = _stub_bin(
        tmp_path, {
            'curl': 'printf "exit 0\\n" > get_helm.sh',
            'sudo': '[ "$1" = -A ] && shift; [ "$1" = -E ] && shift; "$@"',
            'helm': (f'[ "$1" = upgrade ] && exit {helm_upgrade_rc}; exit 0'),
            'kubectl': ('case "$*" in *describe*) '
                        'echo "nvidia.com/gpu: 1 nvidia.com/gpu.product";; '
                        f'*delete*) exit {kubectl_delete_rc};; esac'),
        })
    return subprocess.run(
        ['bash', '-c', deploy._gpu_operator_install_cmd('')],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False)


def test_gpu_operator_cmd_fails_when_helm_fails(tmp_path):
    out = _gpu_install(tmp_path, helm_upgrade_rc=1, kubectl_delete_rc=0)
    assert out.returncode != 0
    assert 'installed successfully' not in out.stdout


def test_gpu_operator_cmd_ignores_legacy_service_delete_failure(tmp_path):
    out = _gpu_install(tmp_path, helm_upgrade_rc=0, kubectl_delete_rc=1)
    assert out.returncode == 0
    assert 'installed successfully' in out.stdout


def test_unchanged_numeric_password_matches_a_master_history(
        monkeypatch, tmp_path):
    """master wrote an unquoted numeric password into the history as an int;
    the same unchanged pools file must not count as a config change."""
    key = tmp_path / 'key'
    key.write_text('k')
    pools = tmp_path / 'pools.yaml'
    pools.write_text(f'p:\n  hosts:\n  - 10.0.0.1\n  user: u\n'
                     f'  identity_file: {key}\n  password: 123456\n')
    # Literal master output (yaml.dump of the loaded config), not ours.
    (tmp_path / 'ssh-p-history.yaml').write_text(
        f'ha_enabled: false\nhosts:\n- 10.0.0.1\nidentity_file: {key}\n'
        'password: 123456\nuser: u\n')
    monkeypatch.setattr(deploy.constants, 'NODE_POOLS_INFO_DIR', str(tmp_path))
    single = mock.Mock(return_value=[])
    monkeypatch.setattr(deploy, 'deploy_single_cluster', single)
    deploy.deploy_multiple_clusters('p',
                                    ssh_node_pools_file=str(pools),
                                    kubeconfig_path=str(tmp_path / 'kc'),
                                    cleanup=False)
    single.assert_called_once()


def test_head_password_change_does_not_print_it(monkeypatch, tmp_path):
    """A per-host password on the head changed: name the field, not values."""
    key = tmp_path / 'key'
    key.write_text('k')

    def pool(password):
        return {
            'hosts': [{
                'ip': '10.0.0.1',
                'password': password
            }],
            'user': 'u',
            'identity_file': str(key),
        }

    pools = tmp_path / 'pools.yaml'
    pools.write_text(json.dumps({'p': pool('new-secret')}))
    (tmp_path / 'ssh-p-history.yaml').write_text(json.dumps(pool('old-secret')))
    monkeypatch.setattr(deploy.constants, 'NODE_POOLS_INFO_DIR', str(tmp_path))
    single = mock.Mock()
    monkeypatch.setattr(deploy, 'deploy_single_cluster', single)
    with pytest.raises(RuntimeError) as e:
        deploy.deploy_multiple_clusters('p',
                                        ssh_node_pools_file=str(pools),
                                        kubeconfig_path=str(tmp_path / 'kc'),
                                        cleanup=False)
    assert 'head node (host 0, 10.0.0.1): password.' in str(e.value)
    assert 'old-secret' not in str(e.value)
    assert 'new-secret' not in str(e.value)
    single.assert_not_called()


@pytest.mark.parametrize('history_password,password,expected', [
    ('old-secret', 'new-secret', "field 'password'."),
    (None, 'x\ny', 'Host 10.0.0.1: the sudo password'),
])
def test_deploy_rejects_password_without_printing_it(monkeypatch, tmp_path,
                                                     history_password, password,
                                                     expected):
    key = tmp_path / 'key'
    key.write_text('k')
    pools = tmp_path / 'pools.yaml'
    pools.write_text(
        json.dumps({
            'p': {
                'hosts': ['10.0.0.1'],
                'user': 'u',
                'identity_file': str(key),
                'password': password,
            }
        }))
    if history_password is not None:
        (tmp_path / 'ssh-p-history.yaml').write_text(
            json.dumps({
                'hosts': ['10.0.0.1'],
                'user': 'u',
                'identity_file': str(key),
                'password': history_password,
            }))
    monkeypatch.setattr(deploy.constants, 'NODE_POOLS_INFO_DIR', str(tmp_path))
    single = mock.Mock()
    monkeypatch.setattr(deploy, 'deploy_single_cluster', single)
    with pytest.raises(RuntimeError) as e:
        deploy.deploy_multiple_clusters('p',
                                        ssh_node_pools_file=str(pools),
                                        kubeconfig_path=str(tmp_path / 'kc'),
                                        cleanup=False)
    assert expected in str(e.value)
    for secret in (history_password, password):
        if secret is not None:
            assert secret not in str(e.value)
    single.assert_not_called()


@pytest.mark.parametrize('is_worker', [False, True])
@pytest.mark.parametrize('installed,fails', [('k3s-agent-uninstall.sh', False),
                                             ('k3s-uninstall.sh', False),
                                             (None, False),
                                             ('k3s-uninstall.sh', True)])
def test_cleanup_runs_the_uninstall_script_the_host_has(monkeypatch, tmp_path,
                                                        is_worker, installed,
                                                        fails):
    """Uninstall what is installed, whatever role the config gives the host;
    a failing uninstall fails the cleanup instead of being reported done."""
    k3s_bin = tmp_path / 'bin'
    k3s_bin.mkdir()
    if installed:
        (k3s_bin / installed).write_text('#!/bin/sh\n')
        (k3s_bin / installed).chmod(0o755)
    stub = tmp_path / 'stub'
    stub.mkdir()
    (stub / 'sudo').write_text(
        f'#!/bin/sh\necho "$@" >> {tmp_path}/sudo.log\n'
        f'case "$2" in *uninstall.sh) exit {int(fails)};; esac\n')
    (stub / 'sudo').chmod(0o755)
    monkeypatch.setattr(deploy, '_K3S_BIN', str(k3s_bin))
    monkeypatch.setattr(deploy, 'force_update_status', lambda *a, **k: None)
    monkeypatch.setattr(deploy, 'success_message', lambda *a, **k: None)
    cmds = []
    monkeypatch.setattr(deploy_utils, 'run_remote',
                        lambda node, cmd, *a, **k: cmds.append(cmd) or 'ok')
    deploy.cleanup_node('10.0.0.1', 'u', 'key', '', is_worker=is_worker)
    proc = subprocess.run(['bash', '-c', cmds[0]],
                          env={'PATH': f'{stub}:/usr/bin:/bin'},
                          check=False)
    calls = (tmp_path / 'sudo.log').read_text().splitlines()
    uninstalls = [c for c in calls if c.endswith('uninstall.sh')]
    assert uninstalls == ([f'-A {k3s_bin}/{installed}'] if installed else [])
    if fails:
        assert proc.returncode != 0
        assert not any(' rm ' in f' {c} ' for c in calls)
    else:
        assert proc.returncode == 0
        assert calls[-1].startswith('-A rm -rf /etc/rancher')


def test_yaml_passwords_are_kept_as_typed(tmp_path):
    """YAML 1.1 reads 012345 as octal 5349 and yes as True."""
    f = tmp_path / 'pools.yaml'
    f.write_text('p:\n'
                 '  password: 012345\n'
                 '  hosts:\n'
                 '  - {ip: 10.0.0.1, password: 1e5}\n'
                 '  - {ip: 10.0.0.2, password: yes}\n'
                 '  - {ip: 10.0.0.3, password: 123456}\n'
                 '  - {ip: 10.0.0.4, password: }\n'
                 '  - {ip: 10.0.0.5, password: null}\n')
    pool = ssh_utils.load_ssh_targets(str(f))['p']
    assert pool['password'] == '012345'
    assert [h['password'] for h in pool['hosts']
           ] == ['1e5', 'yes', '123456', None, None]


def test_ssh_down_keeps_state_when_a_host_is_not_cleaned(monkeypatch, tmp_path):
    key = tmp_path / 'key'
    key.write_text('k')
    pools = tmp_path / 'pools.yaml'
    pools.write_text(
        json.dumps({
            'p': {
                'hosts': ['10.0.0.1'],
                'user': 'u',
                'identity_file': str(key)
            }
        }))
    history = tmp_path / 'ssh-p-history.yaml'
    history.write_text(
        json.dumps({
            'hosts': ['10.0.0.1'],
            'user': 'u',
            'identity_file': str(key)
        }))
    monkeypatch.setattr(deploy.constants, 'NODE_POOLS_INFO_DIR', str(tmp_path))
    monkeypatch.setattr(deploy, 'force_update_status', lambda *a, **k: None)
    # The SSH check passes; only the uninstall command fails.
    monkeypatch.setattr(
        deploy_utils, 'run_remote', lambda node, cmd, *a, **k: None
        if 'uninstall' in cmd else 'ok')
    with pytest.raises(RuntimeError,
                       match='Failed to uninstall k3s on 10.0.0.1'):
        deploy.deploy_multiple_clusters('p',
                                        ssh_node_pools_file=str(pools),
                                        kubeconfig_path=str(tmp_path / 'kc'),
                                        cleanup=True)
    assert history.exists()


class _StopAtKubeconfig(Exception):
    pass


def _deploy_until_kubeconfig(monkeypatch,
                             tmp_path,
                             workers,
                             token_output,
                             cmds=None):
    """Run deploy_single_cluster up to the kubeconfig copy; return commands."""
    cmds = [] if cmds is None else cmds

    def run_remote(node, cmd, *args, **kwargs):
        cmds.append((node, cmd))
        if 'node-token' in cmd:
            return token_output
        if 'ip -4 route' in cmd:
            return '10.0.0.1'
        if 'nvidia-smi' in cmd:
            return None
        return 'SSH connection successful'

    def run_command(cmd, **kwargs):
        if cmd[0] == 'scp':
            raise _StopAtKubeconfig()
        return ''

    monkeypatch.setattr(deploy.constants, 'NODE_POOLS_INFO_DIR', str(tmp_path))
    monkeypatch.setattr(deploy, 'force_update_status', lambda *a, **k: None)
    monkeypatch.setattr(deploy_utils, 'run_remote', run_remote)
    monkeypatch.setattr(deploy_utils, 'run_command', run_command)
    with pytest.raises(_StopAtKubeconfig):
        deploy.deploy_single_cluster('p',
                                     '10.0.0.1',
                                     workers,
                                     'u',
                                     'key',
                                     'ssh-p',
                                     None,
                                     False, [False] * len(workers),
                                     str(tmp_path / 'kc'),
                                     cleanup=False)
    return cmds


def _random_token():
    return f'K10{os.urandom(32).hex()}::server:{os.urandom(16).hex()}'


@pytest.mark.parametrize('workers', [
    ['10.0.0.2'],
    ['10.0.0.2', '10.0.0.3', '10.0.0.4'],
],
                         ids=['agent', 'ha-servers-and-agent'])
def test_joiners_get_the_token_read_from_the_head(monkeypatch, tmp_path,
                                                  workers):
    token = _random_token()
    cmds = _deploy_until_kubeconfig(monkeypatch, tmp_path, workers, token)
    head_install = [
        c for n, c in cmds if 'get.k3s.io' in c and 'K3S_URL' not in c
    ]
    joins = {n: c for n, c in cmds if 'K3S_URL' in c}
    assert len(head_install) == 1
    # The head generates its own token; passing one would replace it.
    assert 'K3S_TOKEN' not in head_install[0]
    assert sorted(joins) == workers
    for cmd in joins.values():
        assert f'K3S_TOKEN={token} ' in cmd


@pytest.mark.parametrize('shared', [True, False], ids=['shared', 'random'])
def test_warns_when_the_pool_still_uses_the_shared_token(
        monkeypatch, tmp_path, shared):
    secret = 'mytoken' if shared else os.urandom(16).hex()
    warnings = []
    monkeypatch.setattr(deploy.logger, 'warning', warnings.append)
    # A single-host pool reads the token too, so it gets the warning.
    _deploy_until_kubeconfig(monkeypatch, tmp_path, [],
                             f'K10{"ab" * 32}::server:{secret}')
    assert any('shared k3s join token' in w for w in warnings) == shared


@pytest.mark.parametrize(
    'output', [
        None,
        '',
        'mytoken',
        f'K10{"ab" * 32}::server:',
        f'K10{"ab" * 32}::node:{"cd" * 16}',
        f'noise\nK10{"ab" * 32}::server:{"cd" * 16}',
    ],
    ids=['none', 'empty', 'short', 'no-secret', 'node-user', 'extra-output'])
def test_unreadable_join_token_stops_before_any_join(monkeypatch, tmp_path,
                                                     output):
    cmds = []
    with pytest.raises(RuntimeError) as e:
        _deploy_until_kubeconfig(monkeypatch, tmp_path, ['10.0.0.2'], output,
                                 cmds)
    assert str(e.value) == ('Failed to read the k3s join token from the head '
                            'node (10.0.0.1).')
    assert not any('K3S_URL' in c for _, c in cmds)


@pytest.mark.parametrize('join', ['agent', 'server'])
def test_failed_join_log_redacts_token_and_password(monkeypatch, tmp_path,
                                                    join):
    key = tmp_path / 'key'
    key.write_text('k')
    password = 'S3cret$pw'
    secret = os.urandom(16).hex()
    token = f'K10{os.urandom(32).hex()}::server:{secret}'
    askpass = deploy.create_askpass_script(password)
    errors = []
    monkeypatch.setattr(deploy_utils.subprocess, 'run', _fake_run([], 1))
    monkeypatch.setattr(deploy_utils.logger, 'error', errors.append)
    monkeypatch.setattr(deploy.logger, 'error', errors.append)
    if join == 'agent':
        deploy.start_agent_node('10.0.0.2', '10.0.0.1', token, 'u', str(key),
                                askpass)
    else:
        deploy.start_server_node('10.0.0.2', '10.0.0.1', token, 'u', str(key),
                                 askpass, '--tls-san=10.0.0.1')
    logged = '\n'.join(errors)
    assert 'K3S_TOKEN=<redacted>' in logged
    assert secret not in logged and password not in logged


@pytest.mark.parametrize('join', ['agent', 'server'])
def test_join_token_cannot_run_shell_commands(monkeypatch, tmp_path, join):
    """A token from a hostile head stays one value; nothing in it runs."""
    pwned = tmp_path / 'pwned'
    token = f'K10{"ab" * 32}::server:x;touch${{IFS}}{pwned};$(touch${{IFS}}{pwned})'
    assert deploy._JOIN_TOKEN_RE.fullmatch(token)
    cmds = []
    monkeypatch.setattr(deploy_utils, 'run_remote',
                        lambda node, cmd, *a, **k: cmds.append(cmd))
    if join == 'agent':
        deploy.start_agent_node('10.0.0.2', '10.0.0.1', token, 'u', 'key', '')
    else:
        deploy.start_server_node('10.0.0.2', '10.0.0.1', token, 'u', 'key', '',
                                 '--tls-san=10.0.0.1')
    assignment = re.search(r'K3S_TOKEN=\S+', cmds[0]).group(0)
    out = subprocess.run(['bash', '-c', f'{assignment} printenv K3S_TOKEN'],
                         capture_output=True,
                         text=True,
                         check=True,
                         cwd=tmp_path)
    assert out.stdout == token + '\n'
    assert not pwned.exists()


def test_failed_command_stderr_is_redacted(monkeypatch):
    secret = os.urandom(16).hex()
    token = f'K10{os.urandom(32).hex()}::server:{secret}'
    stderr = f'boom: K3S_TOKEN={token}\nbare {token} here'
    errors = []
    monkeypatch.setattr(
        deploy_utils.subprocess, 'run',
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, '', stderr))
    monkeypatch.setattr(deploy_utils.logger, 'error', errors.append)
    assert deploy_utils.run_remote('10.0.0.1', 'true') is None
    assert len(errors) == 1
    assert secret not in errors[0]
    assert 'boom: K3S_TOKEN=<redacted>' in errors[0]


@pytest.mark.parametrize('addr', [
    '10.0.0.1;touch${IFS}pwned;#',
    '$(touch${IFS}pwned)',
    '',
    '10.0.0.1 10.0.0.2',
],
                         ids=['semicolon', 'subshell', 'empty', 'two'])
def test_head_address_must_be_an_ip(monkeypatch, tmp_path, addr):
    """The head's reported address goes into every join command."""
    cmds = []
    monkeypatch.setattr(deploy.constants, 'NODE_POOLS_INFO_DIR', str(tmp_path))
    monkeypatch.setattr(deploy, 'force_update_status', lambda *a, **k: None)

    def run_remote(node, cmd, *args, **kwargs):
        cmds.append(cmd)
        if 'ip -4 route' in cmd:
            return addr
        if 'node-token' in cmd:
            return _random_token()
        return None if 'nvidia-smi' in cmd else 'ok'

    monkeypatch.setattr(deploy_utils, 'run_remote', run_remote)
    monkeypatch.setattr(deploy_utils, 'run_command',
                        mock.Mock(side_effect=_StopAtKubeconfig))
    with pytest.raises(RuntimeError, match='internal IP address of the head'):
        deploy.deploy_single_cluster('p', '10.0.0.1', ['10.0.0.2'], 'u', 'key',
                                     'ssh-p', None, False, [False],
                                     str(tmp_path / 'kc'), False)
    assert not any('K3S_URL' in c for c in cmds)
