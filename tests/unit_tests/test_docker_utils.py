"""Tests for docker container initialization on a remote node."""
import os
from pathlib import Path
import subprocess
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
