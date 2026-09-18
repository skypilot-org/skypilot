"""Tests for Slurm._check_compute_credentials.

The check probes every allowed cluster in a bounded parallel pool and batches
`sinfo` + `env` into one SSH session per cluster. These tests pin ordering,
per-cluster failure isolation, the workdir resolution, and the pool bound.
"""
import threading
import time
from unittest import mock
from unittest.mock import patch

import pytest

from sky import exceptions
from sky.adaptors import slurm
from sky.clouds import slurm as slurm_cloud

_SSH = {'hostname': 'login.example.com', 'port': 22, 'user': 'svc'}


def _ssh_config(per_cluster=None):
    """A fake paramiko-style ssh config whose lookup returns per-cluster
    options, defaulting to _SSH."""
    per_cluster = per_cluster or {}
    cfg = mock.MagicMock()
    cfg.lookup.side_effect = lambda c: {
        **_SSH, 'hostname': f'{c}.example.com',
        **per_cluster.get(c, {})
    }
    return cfg


def _client(sinfo='PARTITION AVAIL', env=None, fs_type='nfs'):
    client = mock.MagicMock()
    client.info_and_env.return_value = (sinfo, env or {'HOME': '/home/svc'})
    client.check_dir_shared_fs.return_value = fs_type
    return client


_COMMON_PATCHES = [
    patch('sky.clouds.slurm.slurm_utils.get_identities_only',
          return_value=True),
    patch('sky.clouds.slurm.slurm_utils.get_identity_file',
          return_value='/root/.ssh/key'),
]


@pytest.fixture(autouse=True)
def _common():
    for p in _COMMON_PATCHES:
        p.start()
    yield
    for p in _COMMON_PATCHES:
        p.stop()


@patch('sky.clouds.slurm.skypilot_config.get_effective_region_config',
       return_value=None)
@patch('sky.clouds.slurm.Slurm.existing_allowed_clusters',
       return_value=['c1', 'c2', 'c3'])
@patch('sky.clouds.slurm.slurm_utils.get_slurm_ssh_config')
@patch('sky.clouds.slurm.slurm.SlurmClient')
def test_all_clusters_enabled_in_input_order(mock_client_class, mock_ssh, *_):
    mock_ssh.return_value = _ssh_config()
    mock_client_class.side_effect = lambda *a, **k: _client()

    success, ctx2text = slurm_cloud.Slurm._check_compute_credentials()

    assert success
    assert list(ctx2text) == ['c1', 'c2', 'c3']
    assert all(
        'enabled' in t and 'disabled' not in t for t in ctx2text.values())
    # One client per cluster, built from that cluster's ssh options.
    hosts = sorted(c.args[0] for c in mock_client_class.call_args_list)
    assert hosts == ['c1.example.com', 'c2.example.com', 'c3.example.com']
    for c in mock_client_class.call_args_list:
        assert c.kwargs['slurm_user'] is None


@patch('sky.clouds.slurm.skypilot_config.get_effective_region_config',
       return_value=None)
@patch('sky.clouds.slurm.Slurm.existing_allowed_clusters',
       return_value=['good', 'bad', 'also-good'])
@patch('sky.clouds.slurm.slurm_utils.get_slurm_ssh_config')
@patch('sky.clouds.slurm.slurm.SlurmClient')
def test_one_cluster_failing_does_not_affect_others(mock_client_class, mock_ssh,
                                                    *_):
    mock_ssh.return_value = _ssh_config()

    def make_client(host, *args, **kwargs):
        del args, kwargs
        client = _client()
        if host.startswith('bad.'):
            client.info_and_env.side_effect = exceptions.CommandError(
                255, 'sinfo', 'Failed to get Slurm cluster information.',
                'ssh: connect to host bad.example.com: Connection timed out')
        return client

    mock_client_class.side_effect = make_client

    success, ctx2text = slurm_cloud.Slurm._check_compute_credentials()

    assert success
    assert list(ctx2text) == ['good', 'bad', 'also-good']
    assert ctx2text['good'].endswith('enabled\x1b[0m')
    assert ctx2text['bad'].startswith('disabled. Credential check failed')
    assert 'Connection timed out' in ctx2text['bad']
    assert ctx2text['also-good'].endswith('enabled\x1b[0m')


@patch('sky.clouds.slurm.skypilot_config.get_effective_region_config',
       return_value=None)
@patch('sky.clouds.slurm.Slurm.existing_allowed_clusters',
       return_value=['only'])
@patch('sky.clouds.slurm.slurm_utils.get_slurm_ssh_config')
@patch('sky.clouds.slurm.slurm.SlurmClient')
def test_all_clusters_failing_reports_not_success(mock_client_class, mock_ssh,
                                                  *_):
    mock_ssh.return_value = _ssh_config()
    client = _client()
    client.info_and_env.side_effect = RuntimeError('boom')
    mock_client_class.return_value = client

    success, ctx2text = slurm_cloud.Slurm._check_compute_credentials()

    assert not success
    assert ctx2text['only'].startswith('disabled. Credential check failed')


@patch('sky.clouds.slurm.skypilot_config.get_effective_region_config',
       return_value=None)
@patch('sky.clouds.slurm.Slurm.existing_allowed_clusters',
       return_value=['no-user', 'ok'])
@patch('sky.clouds.slurm.slurm_utils.get_slurm_ssh_config')
@patch('sky.clouds.slurm.slurm.SlurmClient')
def test_missing_ssh_config_key_message(mock_client_class, mock_ssh, *_):
    cfg = mock.MagicMock()

    def lookup(cluster):
        if cluster == 'no-user':
            return {'hostname': 'no-user.example.com', 'port': 22}
        return dict(_SSH)

    cfg.lookup.side_effect = lookup
    mock_ssh.return_value = cfg
    mock_client_class.side_effect = lambda *a, **k: _client()

    success, ctx2text = slurm_cloud.Slurm._check_compute_credentials()

    assert success
    assert ctx2text['no-user'].startswith('disabled. User is missing')
    assert 'enabled' in ctx2text['ok']
    # No client was built for the cluster with the missing key.
    assert mock_client_class.call_count == 1


@pytest.mark.parametrize('fs_type,expected', [
    ('nfs', 'enabled\x1b[0m'),
    ('fuseblk', "filesystem type is 'fuseblk', not a shared filesystem"),
    (None, 'Could not determine filesystem type'),
])
@patch('sky.clouds.slurm.skypilot_config.get_effective_region_config',
       return_value=None)
@patch('sky.clouds.slurm.Slurm.existing_allowed_clusters', return_value=['c'])
@patch('sky.clouds.slurm.slurm_utils.get_slurm_ssh_config')
@patch('sky.clouds.slurm.slurm.SlurmClient')
def test_shared_fs_messages_preserved(mock_client_class, mock_ssh, mock_allowed,
                                      mock_region, fs_type, expected):
    del mock_allowed, mock_region
    mock_ssh.return_value = _ssh_config()
    client = _client(fs_type=fs_type)
    mock_client_class.return_value = client

    success, ctx2text = slurm_cloud.Slurm._check_compute_credentials()

    assert success
    assert expected in ctx2text['c']
    assert ctx2text['c'].startswith('\x1b[32menabled')
    # Home directory is stat'ed when no workdir is configured.
    client.check_dir_shared_fs.assert_called_once_with('/home/svc')


@patch('sky.clouds.slurm.Slurm.existing_allowed_clusters',
       return_value=['a', 'b'])
@patch('sky.clouds.slurm.slurm_utils.get_slurm_ssh_config')
@patch('sky.clouds.slurm.slurm.SlurmClient')
def test_workdir_resolved_per_cluster_against_remote_env(
        mock_client_class, mock_ssh, *_):
    """The configured workdir is read per cluster (in the calling thread)
    and expanded against that cluster's remote env before the stat."""
    mock_ssh.return_value = _ssh_config()
    workdirs = {'a': '$SCRATCH/sky', 'b': None}

    def region_config(cloud, keys, region=None, default_value=None, **_):
        assert cloud == 'slurm' and keys == ('workdir',)
        return workdirs.get(region, default_value)

    clients = {}

    def make_client(host, *args, **kwargs):
        del args, kwargs
        name = host.split('.')[0]
        client = _client(env={
            'HOME': f'/home/{name}',
            'SCRATCH': f'/scr/{name}'
        },
                         fs_type='fuseblk')
        clients[name] = client
        return client

    mock_client_class.side_effect = make_client

    with patch('sky.clouds.slurm.skypilot_config.get_effective_region_config',
               side_effect=region_config):
        success, ctx2text = slurm_cloud.Slurm._check_compute_credentials()

    assert success
    clients['a'].check_dir_shared_fs.assert_called_once_with('/scr/a/sky')
    clients['b'].check_dir_shared_fs.assert_called_once_with('/home/b')
    assert 'workdir ($SCRATCH/sky)' in ctx2text['a']
    assert 'Home directory (~)' in ctx2text['b']


@patch('sky.clouds.slurm.skypilot_config.get_effective_region_config',
       return_value=None)
@patch('sky.clouds.slurm.Slurm.existing_allowed_clusters',
       return_value=[f'c{i}' for i in range(12)])
@patch('sky.clouds.slurm.slurm_utils.get_slurm_ssh_config')
@patch('sky.clouds.slurm.slurm.SlurmClient')
def test_probes_run_in_parallel_with_bounded_pool(mock_client_class, mock_ssh,
                                                  *_):
    mock_ssh.return_value = _ssh_config()
    lock = threading.Lock()
    in_flight = 0
    peak = 0

    def probe():
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return ('PARTITION', {'HOME': '/home/svc'})

    def make_client(*args, **kwargs):
        del args, kwargs
        client = mock.MagicMock()
        client.info_and_env.side_effect = probe
        client.check_dir_shared_fs.return_value = 'nfs'
        return client

    mock_client_class.side_effect = make_client

    start = time.time()
    success, ctx2text = slurm_cloud.Slurm._check_compute_credentials()
    elapsed = time.time() - start

    assert success
    assert len(ctx2text) == 12
    assert list(ctx2text) == [f'c{i}' for i in range(12)]
    # More than one probe ran at a time, and never more than the pool bound.
    assert peak > 1
    assert peak <= slurm_cloud.Slurm._CREDENTIAL_CHECK_MAX_PARALLELISM == 8
    # 12 probes of 50ms at 8-wide take two rounds, not twelve.
    assert elapsed < 12 * 0.05


@patch('sky.clouds.slurm.skypilot_config.get_effective_region_config',
       return_value=None)
@patch('sky.clouds.slurm.Slurm.existing_allowed_clusters',
       return_value=['c1', 'c2', 'c3'])
@patch('sky.clouds.slurm.slurm_utils.get_slurm_ssh_config')
@patch('sky.clouds.slurm.slurm.SlurmClient')
def test_pool_bound_is_passed_to_run_in_parallel(mock_client_class, mock_ssh,
                                                 *_):
    mock_ssh.return_value = _ssh_config()
    mock_client_class.side_effect = lambda *a, **k: _client()

    with patch('sky.clouds.slurm.subprocess_utils.run_in_parallel',
               wraps=slurm_cloud.subprocess_utils.run_in_parallel
              ) as mock_parallel:
        slurm_cloud.Slurm._check_compute_credentials()

    mock_parallel.assert_called_once()
    assert mock_parallel.call_args.kwargs['num_threads'] == 8
    assert len(mock_parallel.call_args.args[1]) == 3


@patch('sky.clouds.slurm.skypilot_config.get_effective_region_config',
       return_value=None)
@patch('sky.clouds.slurm.Slurm.existing_allowed_clusters', return_value=['c'])
@patch('sky.clouds.slurm.slurm_utils.get_slurm_ssh_config')
@patch('sky.clouds.slurm.slurm.SlurmClient')
def test_two_ssh_sessions_per_cluster(mock_client_class, mock_ssh, *_):
    mock_ssh.return_value = _ssh_config()
    client = _client()
    mock_client_class.return_value = client

    slurm_cloud.Slurm._check_compute_credentials()

    client.info_and_env.assert_called_once_with()
    client.check_dir_shared_fs.assert_called_once()
    client.info.assert_not_called()
    client.get_env.assert_not_called()


class TestSlurmClientInfoAndEnv:
    """SlurmClient.info_and_env batches sinfo and env into one session."""

    @staticmethod
    def _client(slurm_user=None):
        return slurm.SlurmClient(ssh_host='login.example.com',
                                 ssh_port=22,
                                 ssh_user='svc',
                                 ssh_key=None,
                                 slurm_user=slurm_user)

    def test_returns_sinfo_and_parsed_env(self):
        client = self._client()
        with patch.object(client,
                          '_run_slurm_cmds',
                          return_value=[(0, 'PARTITION AVAIL\n', ''),
                                        (0, 'HOME=/home/svc\nX=a=b\nnoeq\n', '')
                                       ]) as mock_run:
            info, env = client.info_and_env()
        mock_run.assert_called_once_with(['sinfo', 'env'])
        assert info == 'PARTITION AVAIL\n'
        assert env == {'HOME': '/home/svc', 'X': 'a=b'}

    def test_sinfo_failure_raises_command_error(self):
        client = self._client()
        with patch.object(client,
                          '_run_slurm_cmds',
                          return_value=[(1, '', 'sinfo: error'),
                                        (0, 'HOME=/h', '')]):
            with pytest.raises(exceptions.CommandError):
                client.info_and_env()

    def test_env_failure_returns_empty_env(self):
        client = self._client()
        with patch.object(client,
                          '_run_slurm_cmds',
                          return_value=[(0, 'PARTITION', ''),
                                        (1, '', 'env: denied')]):
            info, env = client.info_and_env()
        assert info == 'PARTITION'
        assert env == {}

    def test_env_matches_get_env_including_submit_user_override(self):
        client = self._client(slurm_user='alice')
        with patch.object(client, 'get_remote_home_dir',
                          return_value='/home/alice'), \
             patch.object(client, '_run_slurm_cmds',
                          return_value=[(0, 'PARTITION', ''),
                                        (0, 'HOME=/root\nUSER=root', '')]), \
             patch.object(client, '_run_slurm_cmd',
                          return_value=(0, 'HOME=/root\nUSER=root', '')):
            _, batched = client.info_and_env()
            single = client.get_env()
        assert batched == single == {
            'HOME': '/home/alice',
            'USER': 'alice',
            'LOGNAME': 'alice'
        }
