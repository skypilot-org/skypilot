"""Tests for docker container initialization on a remote node."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from unittest import mock

import pytest

from sky.provision import docker_utils

_ACR_SERVER = 'myregistry.azurecr.io'
_MSI_ID = ('/subscriptions/sub-123/resourceGroups/my-rg/providers/'
           'Microsoft.ManagedIdentity/userAssignedIdentities/my-identity')


def _make_initializer(docker_config, runs):
    """Returns a DockerInitializer whose runner records every command."""

    def _fake_run(cmd, **kwargs):
        runs.append((cmd, kwargs))
        if 'command -v docker' in cmd:
            return (0, '/usr/bin/docker', '')
        if 'printenv HOME' in cmd:
            return (0, '/root', '')
        if 'SKYPILOT_DOCKER_USER' in cmd:
            return (0, 'SKYPILOT_DOCKER_USER: root', '')
        return (0, '', '')

    runner = mock.MagicMock()
    runner.run.side_effect = _fake_run
    return docker_utils.DockerInitializer(docker_config, runner, '/dev/null')


def _acr_docker_config(password='', with_identity=True):
    config = {
        'container_name': 'sky_container',
        'image': 'myimage:latest',
        'pull_before_run': True,
        'azure_use_managed_identity': True,
        'docker_login_config': {
            'username': '' if not password else 'token-name',
            'password': password,
            'server': _ACR_SERVER,
        },
    }
    if with_identity:
        config['azure_managed_identity'] = _MSI_ID
    return config


def test_acr_empty_password_uses_managed_identity():
    runs = []
    initializer = _make_initializer(_acr_docker_config(), runs)
    initializer.initialize()
    commands = [cmd for cmd, _ in runs]

    install_cmds = [c for c in commands if 'InstallAzureCLIDeb' in c]
    assert install_cmds, 'The Azure CLI should be installed lazily'

    login_cmds = [c for c in commands if 'az login --identity' in c]
    assert len(login_cmds) == 1
    login_cmd = login_cmds[0]
    # The login must not touch the SSH user's persistent az profile.
    assert 'export AZURE_CONFIG_DIR=$(mktemp -d)' in login_cmd
    assert 'az login --help | grep -q -- "--resource-id"' in login_cmd
    assert 'printf %s --resource-id || printf %s --username' in login_cmd
    assert _MSI_ID in login_cmd
    assert 'az acr login --name myregistry' in login_cmd
    assert (f'login {_ACR_SERVER} '
            f'--username {docker_utils.ACR_TOKEN_USERNAME} '
            '--password-stdin') in login_cmd

    pull_cmds = [c for c in commands if ' pull ' in c]
    assert pull_cmds, 'initialize() should have pulled the image'
    # The registry prefix is added to the image automatically.
    assert f'{_ACR_SERVER}/myimage:latest' in pull_cmds[0]


def test_acr_login_without_identity_omits_resource_id():
    runs = []
    initializer = _make_initializer(_acr_docker_config(with_identity=False),
                                    runs)
    initializer.initialize()
    login_cmds = [cmd for cmd, _ in runs if 'az login --identity' in cmd]
    assert len(login_cmds) == 1
    managed_identity_login = login_cmds[0].split('az acr login', 1)[0]
    assert '--resource-id' not in managed_identity_login
    assert '--username' not in managed_identity_login


@pytest.mark.parametrize('azure_use_managed_identity', [None, False])
@pytest.mark.parametrize('with_identity', [False, True])
def test_acr_without_azure_flag_pulls_anonymously(azure_use_managed_identity,
                                                  with_identity):
    runs = []
    config = _acr_docker_config(with_identity=with_identity)
    if azure_use_managed_identity is None:
        config.pop('azure_use_managed_identity')
    else:
        config['azure_use_managed_identity'] = azure_use_managed_identity
    initializer = _make_initializer(config, runs)
    initializer.initialize()
    commands = [cmd for cmd, _ in runs]

    assert not any('InstallAzureCLIDeb' in cmd for cmd in commands)
    assert not any('az ' in cmd for cmd in commands)
    assert not any(' login ' in cmd for cmd in commands)
    assert any(f' pull {_ACR_SERVER}/myimage:latest' in cmd for cmd in commands)


@pytest.mark.parametrize('azure_use_managed_identity', [False, True])
def test_acr_password_takes_precedence_over_managed_identity(
        azure_use_managed_identity):
    runs = []
    config = _acr_docker_config(password='secret')
    config['azure_use_managed_identity'] = azure_use_managed_identity
    initializer = _make_initializer(config, runs)
    initializer.initialize()
    commands = [cmd for cmd, _ in runs]
    assert not any('az login' in c for c in commands)
    assert any(
        f'login --username token-name --password secret {_ACR_SERVER}' in c
        for c in commands)


@pytest.mark.parametrize('with_identity,modern_cli,login_status,docker_status',
                         [
                             (True, True, 0, 0),
                             (True, False, 0, 0),
                             (False, True, 0, 0),
                             (True, True, 7, 0),
                             (True, True, 0, 9),
                         ])
def test_acr_login_shell_and_profile_cleanup(tmp_path, with_identity,
                                             modern_cli, login_status,
                                             docker_status):
    runs = []
    initializer = _make_initializer(
        _acr_docker_config(with_identity=with_identity), runs)
    initializer.initialize()
    login_command = next(cmd for cmd, _ in runs if 'az login --identity' in cmd)
    original_profile = tmp_path / 'original-profile'
    original_profile.mkdir()
    sentinel = original_profile / 'keep'
    sentinel.write_text('original account state')
    env = dict(os.environ,
               AZURE_CONFIG_DIR=str(original_profile),
               ACR_TEST_DIR=str(tmp_path),
               ACR_TEST_MODERN=str(int(modern_cli)),
               ACR_TEST_LOGIN_STATUS=str(login_status),
               ACR_TEST_DOCKER_STATUS=str(docker_status))
    # Execute the generated shell command; stub only the external executables.
    shell_functions = r"""
az() {
    if [ "$2" = "--help" ]; then
        [ "$ACR_TEST_MODERN" = 1 ] && echo --resource-id
        return 0
    fi
    printf '%s\n' "$*" >> "$ACR_TEST_DIR/az-commands"
    printf '%s' "$AZURE_CONFIG_DIR" > "$ACR_TEST_DIR/profile"
    if [ "$1" = login ]; then
        return "$ACR_TEST_LOGIN_STATUS"
    fi
    printf '%s' test-access-token
}
sudo() { "$@"; }
docker() {
    printf '%s\n' "$*" > "$ACR_TEST_DIR/docker-command"
    cat > "$ACR_TEST_DIR/token"
    return "$ACR_TEST_DOCKER_STATUS"
}
"""
    result = subprocess.run(['bash', '-c', shell_functions + login_command],
                            env=env,
                            capture_output=True,
                            text=True,
                            check=False)
    assert result.returncode == (login_status or docker_status), result.stderr
    temporary_profile = Path((tmp_path / 'profile').read_text())
    assert temporary_profile != original_profile
    assert not temporary_profile.exists()
    assert sentinel.read_text() == 'original account state'
    identity_login = (tmp_path / 'az-commands').read_text().splitlines()[0]
    if with_identity:
        flag = '--resource-id' if modern_cli else '--username'
        assert f'{flag} {_MSI_ID}' in identity_login
    else:
        assert '--resource-id' not in identity_login
        assert '--username' not in identity_login
    if login_status:
        assert not (tmp_path / 'docker-command').exists()
    else:
        assert (tmp_path / 'token').read_text() == 'test-access-token'
        assert (tmp_path / 'docker-command').read_text().strip() == (
            f'login {_ACR_SERVER} --username {docker_utils.ACR_TOKEN_USERNAME} '
            '--password-stdin')


def _plain_docker_config():
    return {
        'container_name': 'sky_container',
        'image': 'myimage:latest',
        'pull_before_run': True,
    }


def test_initialize_runs_container_packages_install():
    runs = []
    initializer = _make_initializer(_plain_docker_config(), runs)
    initializer.initialize()
    install = [cmd for cmd, _ in runs if 'sky_apt.lock' in cmd]
    assert len(install) == 1
    # `_run` wraps the command in `docker exec ... /bin/bash -c <login
    # shell -c <cmd>>`; peel both layers and check the command is intact.
    login_shell = shlex.split(install[0])[-1]
    wrapped = shlex.split(login_shell)[-1]
    assert wrapped.endswith(
        f'({docker_utils.CONTAINER_PACKAGES_INSTALL_CMD})'), wrapped
    # The script itself must survive the quoting unchanged.
    assert shlex.split(docker_utils.CONTAINER_PACKAGES_INSTALL_CMD) == [
        'bash', '-lc', docker_utils.CONTAINER_PACKAGES_INSTALL_SCRIPT
    ]


# The install script is exercised against a fake dpkg/apt-get (see
# fake_dpkg_apt.py) that keeps a package database and emulates the Debian
# semantics the script has to cope with. The tests below assert on the
# image state after the script ran and on the conditions from the issue:
# an image's fuse3 must survive, a non-system python3 must not get a
# system one next to it, and a complete image must not need apt at all.
# They deliberately do not check which apt-get commands were run.
_FAKE_TOOLS = Path(__file__).parent / 'fake_dpkg_apt.py'
# Loaded by path: the tests directory is not a package.
_spec = importlib.util.spec_from_file_location('fake_dpkg_apt', _FAKE_TOOLS)
fake_dpkg_apt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fake_dpkg_apt)
_PACKAGES = docker_utils.CONTAINER_PACKAGES.split()
# What the K8s pod setup leaves in an image: fuse3 for the FUSE 3 ABI
# (fusermount3) plus libfuse2 for FUSE 2 clients.
_FUSE_BOTH_ABIS = {'fuse3', 'libfuse2'}
# Ordinary utilities the script may use. They are linked into the fake
# PATH from the host so that the stubs stay the only dpkg, apt-get and
# python3 visible to the script.
_TOOLBOX = ('awk', 'basename', 'cat', 'cut', 'dirname', 'env', 'grep', 'head',
            'id', 'mkdir', 'printf', 'rm', 'sed', 'sh', 'sort', 'tail', 'tee',
            'touch', 'tr', 'uniq', 'wc', 'xargs')


class _FakeImage:
    """A container image state the install script can be run against."""

    def __init__(self,
                 tmp_path: Path,
                 installed=(),
                 removed=(),
                 known=(),
                 python3_on_path: bool = False):
        self.root = tmp_path
        self.bin = tmp_path / 'bin'
        self.bin.mkdir()
        self.tools = tmp_path / 'tools'
        self.tools.mkdir()
        for tool in _TOOLBOX:
            host_tool = shutil.which(tool)
            if host_tool is not None:
                (self.tools / tool).symlink_to(host_tool)
        self.db = tmp_path / 'dpkg.json'
        self.apt_log = tmp_path / 'apt-get.log'
        status = {}
        status.update({p: fake_dpkg_apt.NOT_INSTALLED for p in known})
        status.update({p: fake_dpkg_apt.CONFIG_FILES for p in removed})
        status.update({p: fake_dpkg_apt.INSTALLED for p in installed})
        self.db.write_text(json.dumps(status))
        for tool in ('dpkg-query', 'dpkg', 'apt-get'):
            self._stub(
                tool, f'exec "$SKY_FAKE_PYTHON" "{_FAKE_TOOLS}" '
                f'{tool} "$@"\n')
        self._stub('flock', 'exit 0\n')
        if python3_on_path:
            self._stub('python3', 'exit 0\n')

    def _stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o755)

    def run(self, **extra_env) -> subprocess.CompletedProcess:
        script = docker_utils.CONTAINER_PACKAGES_INSTALL_SCRIPT
        # The lock file is created on the host otherwise.
        assert '/var/tmp/sky_apt.lock' in script
        script = script.replace('/var/tmp/sky_apt.lock',
                                str(self.root / 'apt.lock'))
        # PATH holds the stubs and the toolbox only, so the host's
        # python3, dpkg and apt-get are invisible. A non-login `bash -c`
        # sources no profile that could add to it.
        env = {
            'PATH': f'{self.bin}:{self.tools}',
            'HOME': str(self.root),
            'SKY_FAKE_PYTHON': sys.executable,
            'SKY_FAKE_DPKG_DB': str(self.db),
            'SKY_FAKE_APT_LOG': str(self.apt_log),
            'SKY_FAKE_BIN': str(self.bin),
            **extra_env,
        }
        self.apt_log.write_text('')
        return subprocess.run(['/bin/bash', '-c', script],
                              env=env,
                              capture_output=True,
                              text=True,
                              check=False)

    def status(self):
        return json.loads(self.db.read_text())

    def installed(self):
        return {
            p for p, s in self.status().items() if s == fake_dpkg_apt.INSTALLED
        }

    def apt_commands(self):
        """The apt-get subcommands run (`update`, `install`), in order."""
        return [
            json.loads(line)['command']
            for line in self.apt_log.read_text().splitlines()
        ]

    def has_python3(self) -> bool:
        return (self.bin / 'python3').exists()


def _complete_image(tmp_path, fuse='fuse3', **kwargs):
    """An image that already ships every dependency."""
    return _FakeImage(tmp_path,
                      installed=_PACKAGES + [fuse],
                      known=['fuse', 'fuse3', 'python3-pip'],
                      python3_on_path=True,
                      **kwargs)


def test_bare_image_gets_every_dependency(tmp_path):
    image = _FakeImage(tmp_path)
    result = image.run()
    assert result.returncode == 0, result.stderr
    assert set(_PACKAGES) <= image.installed()
    assert image.has_python3()
    assert _FUSE_BOTH_ABIS <= image.installed()


@pytest.mark.parametrize('fuse', ['fuse3', 'fuse'])
def test_complete_image_needs_no_apt(tmp_path, fuse):
    image = _complete_image(tmp_path, fuse=fuse)
    before = image.status()
    result = image.run()
    assert result.returncode == 0, result.stderr
    assert image.apt_commands() == []
    assert image.status() == before


@pytest.mark.parametrize('missing', _PACKAGES)
def test_existing_fuse3_survives_installing_other_packages(tmp_path, missing):
    image = _FakeImage(tmp_path,
                       installed=[p for p in _PACKAGES if p != missing] +
                       ['fuse3'],
                       known=['fuse', missing],
                       python3_on_path=True)
    result = image.run()
    assert result.returncode == 0, result.stderr
    assert missing in image.installed()
    assert 'fuse3' in image.installed()


def test_non_system_python3_is_not_shadowed_by_a_system_one(tmp_path):
    # A conda or venv python3 on PATH, with one package left to install so
    # that apt does run.
    image = _FakeImage(tmp_path,
                       installed=_PACKAGES[1:] + ['fuse3'],
                       python3_on_path=True)
    result = image.run()
    assert result.returncode == 0, result.stderr
    assert _PACKAGES[0] in image.installed()
    assert 'python3' not in image.installed()
    assert 'python3-pip' not in image.installed()


def test_image_without_python3_gets_one(tmp_path):
    image = _FakeImage(tmp_path, installed=_PACKAGES + ['fuse3'])
    result = image.run()
    assert result.returncode == 0, result.stderr
    assert image.has_python3()
    assert 'fuse3' in image.installed()


def test_fuse_removed_but_not_purged_counts_as_missing(tmp_path):
    image = _FakeImage(tmp_path,
                       installed=_PACKAGES,
                       removed=['fuse'],
                       python3_on_path=True)
    result = image.run()
    assert result.returncode == 0, result.stderr
    assert image.installed() & {'fuse', 'fuse3'}


def test_second_run_on_the_same_image_needs_no_apt(tmp_path):
    image = _FakeImage(tmp_path)
    assert image.run().returncode == 0
    after_first = image.status()
    result = image.run()
    assert result.returncode == 0, result.stderr
    assert image.apt_commands() == []
    assert image.status() == after_first


def test_failed_apt_update_does_not_stop_the_install(tmp_path):
    image = _FakeImage(tmp_path)
    result = image.run(SKY_FAKE_APT_UPDATE_FAILS='1')
    assert result.returncode == 0, result.stderr
    assert 'update' in image.apt_commands()
    assert set(_PACKAGES) <= image.installed()


def test_failed_install_fails_the_setup(tmp_path):
    image = _FakeImage(tmp_path)
    result = image.run(SKY_FAKE_APT_UNAVAILABLE=_PACKAGES[0])
    assert result.returncode != 0
