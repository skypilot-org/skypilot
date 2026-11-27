"""Tests for the in-container signal probe and its error message.

The probe exists because a node can deny every in-container ``kill()`` while
its pods otherwise look healthy (containerd's default AppArmor profile on
``apparmor_restrict_unprivileged_unconfined=1`` hosts). Pinned here:
that a denied probe fails the launch with a message that names the fix, that
a failed probe is tolerated so the check cannot break healthy clusters, and
that the two message variants point at the right fix for their context.
"""
from unittest import mock

import pytest

from sky.provision.kubernetes import instance as k8s_instance
from sky.provision.kubernetes.config import KubernetesError
from sky.ssh_node_pools.deploy import apparmor

_DENIED = (
    k8s_instance._SIGNAL_PROBE_DENIED_CODE  # pylint: disable=protected-access
)


def _pod(name='c-head', node_name=None):
    pod = mock.Mock()
    pod.metadata.name = name
    pod.spec.node_name = node_name
    return pod


def _runner_result(rc, stdout='', stderr=''):
    runner = mock.Mock()
    runner.run.return_value = (rc, stdout, stderr)
    return runner


def _run_check(runner, pods=None, context='some-context', core_api=None):
    pods = pods if pods is not None else [_pod()]
    with mock.patch.object(k8s_instance.command_runner,
                           'KubernetesCommandRunner',
                           return_value=runner):
        if core_api is not None:
            with mock.patch.object(k8s_instance.kubernetes,
                                   'core_api',
                                   return_value=core_api):
                k8s_instance._check_pods_can_signal(  # pylint: disable=protected-access
                    'ns', context, pods)
        else:
            k8s_instance._check_pods_can_signal(  # pylint: disable=protected-access
                'ns', context, pods)


def test_denied_probe_raises_with_cause_and_fix():
    """A denied kill fails the launch and the message says why and how to fix.

    It must name the containerd issue, not a network one: before this check,
    the same node surfaced as `rsync timed out ... ensure the network is
    stable` after ~10 minutes of retries.
    """
    core_api = mock.Mock()
    core_api.read_namespaced_pod.return_value = _pod(node_name='node-1')
    runner = _runner_result(
        _DENIED, stdout='cri-containerd.apparmor.d//&unconfined (enforce)')
    with pytest.raises(KubernetesError) as exc_info:
        _run_check(runner, core_api=core_api)
    msg = str(exc_info.value)
    assert 'node-1' in msg
    assert 'cri-containerd.apparmor.d//&unconfined' in msg
    assert 'containerd' in msg
    assert 'appArmorProfile: Unconfined' in msg
    assert 'kubernetes-troubleshooting' in msg


def test_ssh_pool_context_message_points_at_sky_ssh_up():
    """On an ssh-<pool> context the fix must be re-running `sky ssh up`,
    which installs the patched profile — not asking an admin the user does
    not have (the nodes are the user's own)."""
    core_api = mock.Mock()
    core_api.read_namespaced_pod.return_value = _pod(node_name='node-1')
    runner = _runner_result(
        _DENIED, stdout='cri-containerd.apparmor.d//&unconfined (enforce)')
    with pytest.raises(KubernetesError) as exc_info:
        _run_check(runner, context='ssh-my-pool', core_api=core_api)
    msg = str(exc_info.value)
    assert 'sky ssh up' in msg
    assert 'cluster admin' not in msg


def test_healthy_probe_does_not_raise():
    _run_check(_runner_result(0))


def test_probe_failure_is_tolerated():
    """A probe that itself fails (image without a shell, exec error) must not
    fail the launch: later setup steps surface real problems, and this check
    must never block a cluster it could not measure."""
    _run_check(_runner_result(127, stderr='sh: not found'))


def test_node_name_lookup_failure_is_tolerated():
    """The node name is a re-read that can race pod deletion; the error must
    still be raised, just without the node name."""
    core_api = mock.Mock()
    core_api.read_namespaced_pod.side_effect = RuntimeError('gone')
    runner = _runner_result(
        _DENIED, stdout='cri-containerd.apparmor.d//&unconfined (enforce)')
    with pytest.raises(KubernetesError) as exc_info:
        _run_check(runner, core_api=core_api)
    assert 'node-1' not in str(exc_info.value)


def test_probe_command_detaches_sleep_stdio():
    """The probe's sleep must not hold the exec stream open: without the
    redirects, a denied kill leaves kubectl exec waiting for the surviving
    sleep until the runner timeout, turning a fast check into a slow one."""
    cmd = k8s_instance._SIGNAL_PROBE_CMD  # pylint: disable=protected-access
    assert '</dev/null >/dev/null 2>&1' in cmd
    # And on the denied path the probe must report the AppArmor label, which
    # the error message quotes.
    assert 'attr/current' in cmd


def test_profile_carries_stacked_peer_rules():
    """The profile we install must allow the stacked peer label, and only
    differ from containerd's pre-fix default by exactly that. If the rules
    disappear, `sky ssh up` silently reinstalls the bug."""
    assert (f'signal (send,receive) peer={apparmor.PROFILE_NAME},'
            in apparmor.PROFILE)
    assert (f'signal (send,receive) peer={apparmor.PROFILE_NAME}//&unconfined,'
            in apparmor.PROFILE)
    assert (f'ptrace (trace,tracedby,read,readby) '
            f'peer={apparmor.PROFILE_NAME}//&unconfined,' in apparmor.PROFILE)
    # No wildcard peers: containerd's review of the fix rejected them.
    assert '//&*' not in apparmor.PROFILE


def test_install_command_guards_on_restrict_sysctl():
    """The install must be a no-op on hosts that do not stack profiles
    (Ubuntu <= 24.04 default), so unaffected nodes keep containerd's own
    profile."""
    cmd = apparmor.install_profile_cmd('')
    assert 'apparmor_restrict_unprivileged_unconfined' in cmd
    assert '"1"' in cmd
    # Loading, not just writing: a written-but-unloaded profile fixes nothing
    # until the next reboot.
    assert 'apparmor_parser -r' in cmd
    assert apparmor.PROFILE_NAME in cmd


def test_install_command_looks_up_parser_under_sudo():
    """apparmor_parser usually lives in /usr/sbin, which a non-login SSH
    session's PATH can omit — the availability check must run as root, or
    affected nodes silently skip the fix."""
    cmd = apparmor.install_profile_cmd('')
    assert "sudo -A sh -c 'command -v apparmor_parser" in cmd
    # And the parse itself must run as root too.
    assert 'sudo -A apparmor_parser -r' in cmd


def test_install_command_never_overwrites_a_custom_profile():
    """An existing profile file that differs from ours is an admin override:
    it must be left in place with a warning, not overwritten. One that
    already carries the stacked-peer rule (ours from a previous run, or an
    admin's own fix) is left alone silently."""
    cmd = apparmor.install_profile_cmd('')
    # The exists-then-decide branch precedes any write.
    exists_check = cmd.index(f'[ -f {apparmor.PROFILE_PATH} ]')
    write = cmd.index(f'sudo -A tee {apparmor.PROFILE_PATH}')
    assert exists_check < write
    # Already-fixed profile: silent no-op before any write.
    assert cmd.index('grep -qF') < write
    # Custom profile: skipped with a marker, still no write on that path.
    assert apparmor.SKIPPED_MARKER in cmd
    # `grep -F`: the rule contains no regex metacharacters today, but -F
    # keeps the match literal if that ever changes.
    assert 'grep -qF' in cmd
