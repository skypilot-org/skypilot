"""Tests for docker container initialization on a remote node."""
from unittest import mock

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
    assert f'--resource-id {_MSI_ID}' in login_cmd
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
    assert '--resource-id' not in login_cmds[0]


def test_acr_password_takes_precedence_over_managed_identity():
    runs = []
    initializer = _make_initializer(_acr_docker_config(password='secret'), runs)
    initializer.initialize()
    commands = [cmd for cmd, _ in runs]
    assert not any('az login' in c for c in commands)
    assert any(
        f'login --username token-name --password secret {_ACR_SERVER}' in c
        for c in commands)
