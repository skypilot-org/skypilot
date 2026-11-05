"""Unit tests for how the fluent-bit setup command installs fluent-bit."""

import os
import re
import shutil
import subprocess

import pytest

from sky.logs import agent as logs_agent
from sky.logs.agent import FluentbitAgent
from sky.utils import resources_utils


class _Agent(FluentbitAgent):
    """Concrete FluentbitAgent so get_setup_command can be rendered."""

    def fluentbit_output_config(self, cluster_name):
        del cluster_name  # unused in the stub
        return {'name': 'stdout', 'match': '*'}

    def get_credential_file_mounts(self):
        return {}


@pytest.fixture(name='cmd')
def _cmd():
    return _Agent().get_setup_command(
        resources_utils.ClusterName('test-cluster', 'test-cluster'))


def _code(cmd):
    return '\n'.join(
        line for line in cmd.splitlines() if not line.lstrip().startswith('#'))


@pytest.mark.skipif(shutil.which('bash') is None, reason='needs bash')
def test_command_is_valid_bash(cmd):
    subprocess.run(['bash', '-n', '-c', cmd], check=True)


def test_sudo_is_skipped_as_root(cmd):
    """Root images often ship without sudo."""
    assert 'if [ "$(id -u)" -eq 0 ]; then sky_sudo=""; fi' in cmd
    # Every privileged step goes through $sky_sudo; a bare sudo fails with
    # 'command not found' on such images.
    bare = [
        line for line in _code(cmd).splitlines()
        if re.search(r'(?<![\w$])sudo\s', line) and
        'sky_sudo=sudo' not in line and 'command -v sudo' not in line
    ]
    assert not bare


def test_rpm_images_install_from_the_distro_repository(cmd):
    assert ('elif command -v dnf >/dev/null 2>&1 || '
            'command -v yum >/dev/null 2>&1 || '
            'command -v microdnf >/dev/null 2>&1; then') in cmd
    for distro, repo in (('rocky', 'rockylinux'), ('almalinux', 'almalinux'),
                         ('centos|rhel|ol', 'centos'), ('amzn', 'amazonlinux')):
        assert f'{distro}) sky_rpm_repo={repo}/$sky_rpm_os_major ;;' in cmd
    assert '"baseurl=https://packages.fluentbit.io/$sky_rpm_repo/"' in cmd
    assert 'gpgcheck=1 repo_gpgcheck=1' in cmd
    assert '| $sky_sudo tee /etc/yum.repos.d/fluent-bit.repo' in cmd


def test_rpm_install_is_pinned_below_5(cmd):
    assert '"$sky_rpm_mgr" install -y "fluent-bit-4.*"' in cmd


def test_rpm_install_ignores_the_image_loader_environment(cmd):
    """A conda or CUDA image's libraries can break dnf itself."""
    assert ('env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONPATH -u PYTHONHOME'
            in cmd)


_ROCKY = 'ID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.4"\n'


def _install(tmp_path, *, uid=0, tools=('dnf',), os_release=_ROCKY):
    """Runs the fluent-bit install snippet under bash with stubbed tools.

    Only `tools` (plus `id`, `tee` and a few real coreutils) are on PATH, so
    the host's own package manager and sudo are invisible. Returns the exit
    code, stderr, the commands the stubs saw and what was written via tee.
    """
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    calls = tmp_path / 'calls'
    stubs = {
        'id': f'echo {uid}',
        'tee': 'cat > "$CALLS.tee"; echo "tee $*" >> "$CALLS"',
        'sudo': 'echo "sudo $*" >> "$CALLS"; exec "$@"',
        'dnf': 'echo "dnf $*" >> "$CALLS"',
        'apt-get': 'echo "apt-get $*" >> "$CALLS"',
    }
    for name in ('id', 'tee') + tuple(tools):
        path = bin_dir / name
        path.write_text(f'#!/bin/sh\n{stubs[name]}\n')
        path.chmod(0o755)
    for name in ('cat', 'cut', 'env', 'grep', 'tr'):
        os.symlink(shutil.which(name), bin_dir / name)
    release = tmp_path / 'os-release'
    if os_release is not None:
        release.write_text(os_release)
    snippet = logs_agent._INSTALL_FLUENTBIT.replace('/etc/os-release',
                                                    str(release))
    proc = subprocess.run([shutil.which('bash'), '-c', f'set -e; {snippet}'],
                          env={
                              'PATH': str(bin_dir),
                              'CALLS': str(calls)
                          },
                          capture_output=True,
                          text=True,
                          check=False)
    seen = calls.read_text().splitlines() if calls.exists() else []
    tee = tmp_path / 'calls.tee'
    written = tee.read_text() if tee.exists() else ''
    return proc.returncode, proc.stderr, seen, written


_needs_bash = pytest.mark.skipif(any(
    shutil.which(t) is None
    for t in ('bash', 'cat', 'cut', 'env', 'grep', 'tr')),
                                 reason='needs bash and coreutils')


@_needs_bash
def test_root_runs_dnf_without_sudo(tmp_path):
    rc, _, seen, written = _install(tmp_path)
    assert rc == 0
    assert not any(line.startswith('sudo ') for line in seen)
    assert seen[-1] == 'dnf install -y fluent-bit-4.*'
    assert 'baseurl=https://packages.fluentbit.io/rockylinux/9/' in written


@_needs_bash
def test_non_root_runs_dnf_through_sudo(tmp_path):
    rc, _, seen, _ = _install(tmp_path, uid=1000, tools=('dnf', 'sudo'))
    assert rc == 0
    assert any(
        line.startswith('sudo env ') and 'install -y fluent-bit-4.*' in line
        for line in seen)
    assert 'sudo tee /etc/yum.repos.d/fluent-bit.repo' in seen


@_needs_bash
def test_rhel_rebuilds_use_the_centos_repository(tmp_path):
    rc, _, _, written = _install(
        tmp_path,
        os_release='ID="cloudlinux"\nID_LIKE="rhel fedora centos"\n'
        'VERSION_ID="8.10"\n')
    assert rc == 0
    assert 'baseurl=https://packages.fluentbit.io/centos/8/' in written


@pytest.mark.parametrize('kwargs,reason', [
    ({
        'tools': ()
    }, 'no apt-get, dnf or yum on this node'),
    ({
        'uid': 1000
    }, 'not root and no sudo'),
    ({
        'os_release': 'ID=fedora\nVERSION_ID=40\n'
    }, "no fluent-bit packages for 'fedora'"),
    ({
        'os_release': None
    }, 'os-release is missing'),
    ({
        'tools': ('apt-get',),
        'os_release': 'ID=linuxmint\nVERSION_CODENAME=wilma\n'
    }, "no fluent-bit packages for 'linuxmint'"),
])
@_needs_bash
def test_unsupported_platforms_exit_with_their_own_code(tmp_path, kwargs,
                                                        reason):
    rc, stderr, seen, _ = _install(tmp_path, **kwargs)
    assert rc == logs_agent.UNSUPPORTED_PLATFORM_EXIT_CODE
    assert reason in stderr
    assert not any('install' in line for line in seen)
