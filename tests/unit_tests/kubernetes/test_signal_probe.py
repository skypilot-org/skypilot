"""Tests for the in-container signal probe and its error message.

The probe exists because a node can deny every in-container ``kill()`` while
its pods otherwise look healthy (containerd's default AppArmor profile on
``apparmor_restrict_unprivileged_unconfined=1`` hosts). Pinned here:
that a denied probe fails the launch with a message that names the fix, that
a failed probe is tolerated so the check cannot break healthy clusters, and
that the two message variants point at the right fix for their context.
"""
import os
import subprocess
import tempfile
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
    already carries the stacked-peer signal rule (an admin's own fix) is
    left alone silently."""
    cmd = apparmor.install_profile_cmd('')
    # The compare-then-decide branch precedes any write.
    cmp_check = cmd.index(f'cmp -s "$tmp" {apparmor.PROFILE_PATH}')
    install = cmd.index(f'cp "$tmp" {apparmor.STAGING_PATH}')
    assert cmp_check < install
    # Already-fixed profile: silent no-op before any write.
    assert cmd.index('grep -qF') < install
    # Custom profile: skipped with a marker, still no write on that path.
    assert apparmor.SKIPPED_MARKER in cmd


def test_install_command_reloads_our_own_profile():
    """A profile byte-identical to ours (previous `sky ssh up`) must be
    reloaded, not trusted from disk: AppArmor enforces the kernel's loaded
    profile, so a previous run whose `apparmor_parser -r` failed must retry
    the load on the next `sky ssh up`, or the node stays broken while the
    file looks fixed."""
    cmd = apparmor.install_profile_cmd('')
    install = cmd.index(f'cp "$tmp" {apparmor.STAGING_PATH}')
    # Last occurrence: the final reload after the staged install (an earlier
    # one exists on the config-managed symlink branch).
    reload_ = cmd.rindex(f'apparmor_parser -r {apparmor.PROFILE_PATH}')
    # The reload is unconditional after the install: identical files flow
    # through cp + apparmor_parser -r again.
    assert reload_ > install
    # And a config-managed symlink is detected before any write, so the
    # link is reloaded through, never replaced.
    assert cmd.index(f'[ -L {apparmor.PROFILE_PATH} ]') < install


def test_install_command_matches_the_signal_rule_not_ptrace():
    """The already-fixed check must match the full signal rule: the bare
    stacked peer string also appears in the ptrace rule and in comments,
    and neither of those permits signals."""
    assert apparmor.STACKED_PEER_SIGNAL_RULE.startswith('signal (send,receive)')
    # A profile carrying only the ptrace rule (signal rule removed) must
    # not satisfy the check.
    without_signal = apparmor.PROFILE.replace(
        apparmor.STACKED_PEER_SIGNAL_RULE + ',', '', 1)
    assert apparmor.STACKED_PEER_SIGNAL_RULE not in without_signal
    assert '//&unconfined' in without_signal  # the ptrace rule is still there


def test_install_command_branch_decisions():
    """Runs the installer's branch logic for each kind of existing profile.

    Guards (sysctl, parser lookup) read host state and would no-op the
    command in CI, so the test strips them and executes the body with
    stubbed ``sudo`` and ``apparmor_parser``. Pins the outcomes: install,
    reload-ours, leave an admin fix alone, warn on custom, never break a
    config-managed symlink, and clean up staging leftovers."""
    cmd = apparmor.install_profile_cmd('')
    # Body after the guard `if ...; then`, before its closing `fi`.
    body = cmd.split('then', 1)[1].rsplit('fi', 1)[0]

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, apparmor.PROFILE_NAME)
        staging = os.path.join(d, f'.{apparmor.PROFILE_NAME}.skypilot-new')
        # sudo shim: drop the -A and exec the rest.
        with open(os.path.join(d, 'sudo'), 'w') as f:
            f.write('#!/bin/bash\n[ "$1" = "-A" ] && shift\nexec "$@"\n')
        os.chmod(os.path.join(d, 'sudo'), 0o755)
        # Parser shim records the reload in a marker file (stdout is what
        # the assertions compare against).
        with open(os.path.join(d, 'apparmor_parser'), 'w') as f:
            f.write(f'#!/bin/bash\ntouch {d}/parser-ran\n')
        os.chmod(os.path.join(d, 'apparmor_parser'), 0o755)
        parser_marker = os.path.join(d, 'parser-ran')
        env = dict(os.environ, PATH=f'{d}:/usr/bin:/bin')

        script = body.replace(apparmor.PROFILE_PATH,
                              path).replace(apparmor.STAGING_PATH, staging)

        def run(existing, preexisting_staging=False):
            for stale in (path, staging, parser_marker):
                if os.path.lexists(stale):
                    os.unlink(stale)
            if preexisting_staging:
                with open(staging, 'w') as f:
                    f.write('leftover from a killed run')
            if existing is not None:
                with open(path, 'w') as f:
                    f.write(existing)
            r = subprocess.run(['bash', '-c', script],
                               env=env,
                               capture_output=True,
                               text=True,
                               check=True)
            return (r.stdout.strip(), os.path.exists(staging),
                    os.path.exists(parser_marker))

        # Missing profile: install, load, and a leftover staging file from a
        # killed earlier run must not survive the new one.
        out, leftover, parsed = run(None, preexisting_staging=True)
        assert (apparmor.INSTALLED_MARKER, False,
                True) == (out, leftover,
                          parsed), ('missing profile must install and load, '
                                    'and consume the staging leftover')
        with open(path) as f:
            assert f.read() == apparmor.PROFILE

        out, _, parsed = run(apparmor.PROFILE)
        assert (apparmor.INSTALLED_MARKER in out,
                True) == (True, parsed), ('identical profile must reload')

        admin_fixed = apparmor.PROFILE.replace(
            '# allow processes', f'  {apparmor.STACKED_PEER_SIGNAL_RULE},\n'
            '  # allow processes')
        out, _, parsed = run(admin_fixed)
        assert (out, parsed) == ('', False), (
            'admin-fixed profile must be left alone silently')

        ptrace_only = apparmor.PROFILE.replace(
            apparmor.STACKED_PEER_SIGNAL_RULE + ',', '', 1)
        out, _, parsed = run(ptrace_only)
        assert (apparmor.SKIPPED_MARKER in out, False) == (True, parsed), (
            'profile without the signal rule must be skipped with a warning')

        # A config-managed symlink is never replaced by the install, even
        # when its target is byte-identical to ours: the reload goes through
        # the link, and the link itself must survive.
        target = os.path.join(d, 'managed-profile')
        with open(target, 'w') as f:
            f.write(apparmor.PROFILE)
        for stale in (path, staging, parser_marker):
            if os.path.lexists(stale):
                os.unlink(stale)
        os.symlink(target, path)
        r = subprocess.run(['bash', '-c', script],
                           env=env,
                           capture_output=True,
                           text=True,
                           check=True)
        assert apparmor.INSTALLED_MARKER in r.stdout
        assert os.path.islink(path), 'symlink must not be replaced'
        assert os.path.exists(parser_marker), 'reload must go through the link'
