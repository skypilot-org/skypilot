"""Unit tests for how the fluent-bit setup command installs fluent-bit."""

import re
import shutil
import subprocess

import pytest

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
        if re.search(r'(?<![\w$])sudo\s', line) and 'sky_sudo=sudo' not in line
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


def test_unsupported_package_manager_fails_with_a_reason(cmd):
    assert ('SkyPilot: cannot install fluent-bit: no apt-get, dnf or yum on '
            'this node') in cmd
