"""Unit tests for sky/ssh_node_pools/deploy/deploy.py helpers."""

import re

from sky.ssh_node_pools.deploy import deploy


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
    # The sibling `_dcgm_exporter_service_cmd` correctly omits it.
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


def test_tcp_forwarding_cmd_runs_sudo_under_askpass():
    """Regression for #10303: every sudo here must be reachable without a tty.

    Reading `sshd -T` and rewriting sshd_config need root. When the node has
    no passwordless sudo, a bare `sudo` aborts with "a terminal is required to
    read the password" and the deploy fails before k3s is ever installed.
    """
    askpass_block = 'echo "askpass"'
    cmd = deploy._tcp_forwarding_cmd(askpass_block, 'head.example.com')

    # The askpass block must be present verbatim, as in every sibling helper.
    assert askpass_block in cmd

    # It must come first: SUDO_ASKPASS has to be exported before any sudo runs.
    assert cmd.index(askpass_block) < cmd.index('sudo')

    # Every sudo invocation must pass -A so it consults SUDO_ASKPASS instead
    # of trying to prompt on a tty that ssh did not allocate.
    sudos = re.findall(r'sudo(?: -\S+)*', cmd)
    assert sudos, cmd
    assert all(s.startswith('sudo -A') for s in sudos), sudos

    # The behaviour itself is unchanged.
    assert 'allowtcpforwarding' in cmd
    assert '/etc/ssh/sshd_config' in cmd
    assert 'systemctl restart sshd' in cmd
    assert 'head.example.com' in cmd


def test_tcp_forwarding_cmd_without_password_has_no_askpass_block():
    """Guard against over-fixing: passwordless hosts must be unaffected.

    With no password there is no askpass block to prepend, and `sudo -A` is a
    no-op because sudo never needs to prompt -- the same reason every other
    privileged block here passes -A unconditionally.
    """
    cmd = deploy._tcp_forwarding_cmd('', 'head.example.com')
    assert 'SUDO_ASKPASS' not in cmd
    assert cmd.lstrip().startswith('if [')
