"""Tests for the remote Slurm cluster registration core."""
import os
import stat

from paramiko.config import SSHConfig
import pytest

from sky.provision.slurm import registration


@pytest.fixture
def manager(tmp_path):
    """A SlurmClusterManager pointed at an isolated tmp directory."""
    m = registration.SlurmClusterManager()
    m.config_path = str(tmp_path / 'config')
    m.keys_dir = str(tmp_path / 'keys')
    m.known_hosts_dir = str(tmp_path / 'known_hosts')
    return m


def _resolved(manager, name):
    return SSHConfig.from_path(manager.config_path).lookup(name)


class TestRegisterCluster:

    def test_register_writes_parseable_config(self, manager):
        manager.register_cluster('slurm-a',
                                 host='10.0.0.1',
                                 user='ubuntu',
                                 identity_file='PRIVKEY-A',
                                 port=2222,
                                 host_key='10.0.0.1 ssh-ed25519 AAAAKEY',
                                 proxy_jump='bastion')

        # paramiko (the parser slurm actually uses) round-trips it.
        resolved = _resolved(manager, 'slurm-a')
        assert resolved['hostname'] == '10.0.0.1'
        assert resolved['user'] == 'ubuntu'
        assert resolved['port'] == '2222'
        assert resolved['proxyjump'] == 'bastion'
        assert resolved['identitiesonly'] == 'yes'
        # Host-key pinning is expressed when host_key is provided.
        assert resolved['stricthostkeychecking'] == 'yes'
        assert 'userknownhostsfile' in resolved

    def test_identity_file_is_0600(self, manager):
        manager.register_cluster('slurm-a',
                                 host='10.0.0.1',
                                 user='ubuntu',
                                 identity_file='PRIVKEY-A')
        key_path = os.path.join(manager.keys_dir, 'slurm-a')
        assert os.path.exists(key_path)
        assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600
        assert open(key_path).read().startswith('PRIVKEY-A')
        # IdentityFile in the config points at the stored key.
        assert _resolved(manager, 'slurm-a')['identityfile'] == [key_path]

    def test_no_host_key_omits_pinning(self, manager):
        manager.register_cluster('slurm-a',
                                 host='10.0.0.1',
                                 user='ubuntu',
                                 identity_file='PRIVKEY-A')
        resolved = _resolved(manager, 'slurm-a')
        assert 'stricthostkeychecking' not in resolved
        assert 'userknownhostsfile' not in resolved

    def test_upsert_does_not_duplicate_block(self, manager):
        manager.register_cluster('slurm-a',
                                 host='10.0.0.1',
                                 user='ubuntu',
                                 identity_file='PRIVKEY-A')
        manager.register_cluster('slurm-a',
                                 host='10.9.9.9',
                                 user='ubuntu',
                                 identity_file='PRIVKEY-A2')
        content = open(manager.config_path).read()
        assert content.count('BEGIN skypilot-managed slurm-a') == 1
        # The updated value wins.
        assert _resolved(manager, 'slurm-a')['hostname'] == '10.9.9.9'

    def test_multiple_clusters_coexist(self, manager):
        manager.register_cluster('slurm-a',
                                 host='10.0.0.1',
                                 user='ubuntu',
                                 identity_file='K')
        manager.register_cluster('slurm-b',
                                 host='10.0.0.2',
                                 user='root',
                                 identity_file='K')
        names = [
            h for h in SSHConfig.from_path(manager.config_path).get_hostnames()
            if h != '*'
        ]
        assert sorted(names) == ['slurm-a', 'slurm-b']

    def test_preserves_unmanaged_content(self, manager):
        # A hand-authored block outside the sentinel markers must survive.
        os.makedirs(os.path.dirname(manager.config_path), exist_ok=True)
        with open(manager.config_path, 'w') as f:
            f.write('Host hand-edited\n    HostName 1.2.3.4\n')
        manager.register_cluster('slurm-a',
                                 host='10.0.0.1',
                                 user='ubuntu',
                                 identity_file='K')
        content = open(manager.config_path).read()
        assert 'Host hand-edited' in content
        assert _resolved(manager, 'hand-edited')['hostname'] == '1.2.3.4'
        assert _resolved(manager, 'slurm-a')['hostname'] == '10.0.0.1'

    @pytest.mark.parametrize('bad_name', ['../evil', 'a/b', '', 'a b'])
    def test_invalid_names_rejected(self, manager, bad_name):
        with pytest.raises(ValueError):
            manager.register_cluster(bad_name,
                                     host='10.0.0.1',
                                     user='ubuntu',
                                     identity_file='K')

    @pytest.mark.parametrize('missing', ['host', 'user', 'identity_file'])
    def test_required_fields(self, manager, missing):
        kwargs = {
            'host': '10.0.0.1',
            'user': 'ubuntu',
            'identity_file': 'K',
        }
        kwargs[missing] = ''
        with pytest.raises(ValueError):
            manager.register_cluster('slurm-a', **kwargs)


class TestDeleteCluster:

    def test_delete_removes_block_and_files(self, manager):
        manager.register_cluster('slurm-a',
                                 host='10.0.0.1',
                                 user='ubuntu',
                                 identity_file='K',
                                 host_key='hostkey')
        key_path = os.path.join(manager.keys_dir, 'slurm-a')
        kh_path = os.path.join(manager.known_hosts_dir, 'slurm-a')
        assert os.path.exists(key_path) and os.path.exists(kh_path)

        assert manager.delete_cluster('slurm-a') is True
        names = [
            h for h in SSHConfig.from_path(manager.config_path).get_hostnames()
            if h != '*'
        ]
        assert names == []
        assert not os.path.exists(key_path)
        assert not os.path.exists(kh_path)

    def test_delete_missing_returns_false(self, manager):
        manager.register_cluster('slurm-a',
                                 host='10.0.0.1',
                                 user='ubuntu',
                                 identity_file='K')
        assert manager.delete_cluster('slurm-b') is False
        # slurm-a is untouched.
        assert _resolved(manager, 'slurm-a')['hostname'] == '10.0.0.1'

    def test_delete_leaves_other_clusters(self, manager):
        manager.register_cluster('slurm-a',
                                 host='10.0.0.1',
                                 user='ubuntu',
                                 identity_file='K')
        manager.register_cluster('slurm-b',
                                 host='10.0.0.2',
                                 user='root',
                                 identity_file='K')
        manager.delete_cluster('slurm-a')
        names = [
            h for h in SSHConfig.from_path(manager.config_path).get_hostnames()
            if h != '*'
        ]
        assert names == ['slurm-b']


class TestListClusters:

    def test_list_empty_when_no_file(self, manager):
        assert manager.list_clusters() == {}

    def test_list_returns_non_secret_detail(self, manager):
        manager.register_cluster('slurm-a',
                                 host='10.0.0.1',
                                 user='ubuntu',
                                 identity_file='SECRET',
                                 port=2222,
                                 proxy_jump='bastion')
        clusters = manager.list_clusters()
        assert set(clusters) == {'slurm-a'}
        entry = clusters['slurm-a']
        assert entry['host'] == '10.0.0.1'
        assert entry['user'] == 'ubuntu'
        assert entry['port'] == 2222
        assert entry['proxy_jump'] == 'bastion'
        assert entry['managed'] is True
        # The private key contents are never exposed via listing.
        assert 'SECRET' not in str(clusters)
