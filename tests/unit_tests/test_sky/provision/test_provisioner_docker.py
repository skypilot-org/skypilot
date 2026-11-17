"""Cloud-specific credentials passed from provisioning to Docker setup."""
# pylint: disable=protected-access
import contextlib
from types import SimpleNamespace
from unittest import mock

import pytest

from sky import clouds
from sky.provision import common
from sky.provision import provisioner
from sky.utils import resources_utils


@pytest.mark.parametrize('cloud', [clouds.Azure(), clouds.AWS(), clouds.GCP()])
@pytest.mark.parametrize('msi',
                         [None, '/subscriptions/sub/identities/test-msi'])
def test_acr_identity_is_enabled_only_for_azure(monkeypatch, cloud, msi):
    config = {
        'provider': {},
        'docker': {
            'image': 'registry.azurecr.io/image:latest',
            'container_name': 'sky_container',
        },
    }
    cluster_info = common.ClusterInfo(
        instances={
            'head': [common.InstanceInfo('head', '10.0.0.1', '1.2.3.4', {})]
        },
        head_instance_id='head',
        provider_name=repr(cloud).lower(),
        provider_config={} if msi is None else {'msi': msi})
    record = common.ProvisionRecord(repr(cloud), 'region', None, 'cluster',
                                    'head', [], ['head'])
    monkeypatch.setattr(provisioner.global_user_state, 'get_cluster_yaml_dict',
                        mock.Mock(return_value=config))
    monkeypatch.setattr(provisioner.global_user_state,
                        'get_handle_from_cluster_name', mock.Mock())
    monkeypatch.setattr(provisioner.global_user_state, 'update_cluster_handle',
                        mock.Mock())
    monkeypatch.setattr(provisioner.provision, 'get_cluster_info',
                        mock.Mock(return_value=cluster_info))
    monkeypatch.setattr(provisioner.backend_utils, 'ssh_credential_from_yaml',
                        mock.Mock(return_value={}))
    monkeypatch.setattr(provisioner, 'wait_for_ssh', mock.Mock())
    monkeypatch.setattr(provisioner.rich_utils, 'safe_status',
                        lambda *_: contextlib.nullcontext(mock.Mock()))
    initialize_docker = mock.Mock(return_value='root')
    monkeypatch.setattr(provisioner.instance_setup, 'initialize_docker',
                        initialize_docker)
    # Stop before unrelated runtime installation, after Docker setup completes.
    monkeypatch.setattr(
        provisioner.instance_setup, 'internal_file_mounts',
        mock.Mock(side_effect=RuntimeError('docker configured')))
    with pytest.raises(RuntimeError, match='docker configured'):
        provisioner._post_provision_setup(
            SimpleNamespace(cloud=cloud),
            resources_utils.ClusterName('cluster', 'cluster'), '/cluster.yaml',
            record, None)

    initialize_docker.assert_called_once()
    docker_config = initialize_docker.call_args.kwargs['docker_config']
    if isinstance(cloud, clouds.Azure):
        assert docker_config['azure_use_managed_identity'] is True
        assert docker_config.get('azure_managed_identity') == msi
    else:
        assert 'azure_use_managed_identity' not in docker_config
        assert 'azure_managed_identity' not in docker_config
