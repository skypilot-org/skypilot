"""Unit tests for sky.provision.slurm.utils."""
import json
import os
import shlex
import subprocess
import sys
from unittest import mock

import pytest

from sky import exceptions
from sky.adaptors import slurm as slurm_adaptor
from sky.provision.slurm import utils


class TestSrunSshdCommand:
    """Tests for accelerator environment forwarding through SSH."""

    @pytest.mark.parametrize('openssh_version,expected_setenv', [
        ('9.0', 'SetEnv=CUDA_VISIBLE_DEVICES=4,5'),
        ('7.4', None),
    ])
    def test_openssh_forwards_accelerator_environment(self, tmp_path,
                                                      openssh_version,
                                                      expected_setenv):
        command = utils.srun_sshd_command(
            job_id='123',
            target_node='node-1',
            unix_user='alice',
            cluster_name_on_cloud='cluster',
            is_container_image=False,
        )

        args = shlex.split(command)
        assert args[:8] == [
            'srun', '--quiet', '--unbuffered', '--overlap', '--jobid', '123',
            '-w', 'node-1'
        ]
        assert args[8:10] == ['/bin/bash', '-c']
        bootstrap = args[10]
        assert 'CUDA_VISIBLE_DEVICES' in bootstrap
        assert 'ROCR_VISIBLE_DEVICES' in bootstrap
        assert 'ZE_AFFINITY_MASK' in bootstrap
        assert 'GPU_DEVICE_ORDINAL' in bootstrap
        assert 'SetEnv=$SSHD_SET_ENV' in bootstrap
        assert 'exec "$@"' in bootstrap

        # Execute the bootstrap with sshd replaced by a version/argv probe.
        # Modern servers receive one SetEnv directive, while old servers keep
        # working without it. Unsafe values are never added to either path.
        probe = tmp_path / 'sshd'
        probe.write_text(f"""#!{sys.executable}
import json
import sys
if sys.argv[1:] == ['-V']:
    print('OpenSSH_{openssh_version}', file=sys.stderr)
else:
    print(json.dumps(sys.argv[1:]))
""")
        probe.chmod(0o755)
        bootstrap = bootstrap.replace('SSHD=/usr/sbin/sshd',
                                      f'SSHD={shlex.quote(str(probe))}', 1)
        env = {
            **os.environ,
            'CUDA_VISIBLE_DEVICES': '4,5',
            'ROCR_VISIBLE_DEVICES': 'unsafe value',
        }
        result = subprocess.run(['/bin/bash', '-c', bootstrap],
                                env=env,
                                check=True,
                                capture_output=True,
                                text=True)
        sshd_args = json.loads(result.stdout)
        if expected_setenv is None:
            assert not any(arg.startswith('SetEnv=') for arg in sshd_args)
        else:
            assert sshd_args[-2:] == ['-o', expected_setenv]

    @pytest.mark.parametrize('dropbear_version,expected_flag', [
        ('2025.89', '-e'),
        ('2020.81', ''),
    ])
    def test_dropbear_forwards_only_accelerator_environment(
            self, tmp_path, dropbear_version, expected_flag):
        command = utils.srun_sshd_command(
            job_id='123',
            target_node='node-1',
            unix_user='alice',
            cluster_name_on_cloud='cluster',
            is_container_image=True,
        )

        bootstrap = shlex.split(command)[-1]
        assert '"$DROPBEAR" -V 2>&1' in bootstrap
        assert 'DROPBEAR_VERSION_YEAR > 2022' in bootstrap
        assert 'DROPBEAR_ENV_FLAG=(-e)' in bootstrap
        assert 'env -i "${DROPBEAR_ENV[@]}" "$DROPBEAR"' in bootstrap
        assert '"${DROPBEAR_ENV_FLAG[@]}" -F -s -R' in bootstrap
        assert ('for NAME in CUDA_VISIBLE_DEVICES ROCR_VISIBLE_DEVICES'
                in bootstrap)
        assert 'GPU_DEVICE_ORDINAL LD_LIBRARY_PATH' in bootstrap

        probe = tmp_path / 'dropbear'
        probe.write_text(f"""#!{sys.executable}
import sys
print('Dropbear v{dropbear_version}', file=sys.stderr)
""")
        probe.chmod(0o755)
        setup = bootstrap.split('while :; do', 1)[0]
        setup += ('printf "flag=<%s>\\n" "${DROPBEAR_ENV_FLAG[*]}"; '
                  'printf "env=<%s>\\n" "${DROPBEAR_ENV[*]}"')
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in ('CUDA_VISIBLE_DEVICES', 'ROCR_VISIBLE_DEVICES',
                           'ZE_AFFINITY_MASK', 'GPU_DEVICE_ORDINAL',
                           'LD_LIBRARY_PATH')
        }
        env.update({
            'PATH': f'{tmp_path}:{env["PATH"]}',
            'CUDA_VISIBLE_DEVICES': '4,5',
            'LD_LIBRARY_PATH': '/opt/accelerator/lib',
            'SLURM_JOB_ID': 'must-not-be-forwarded',
        })
        result = subprocess.run(['/bin/bash', '-c', setup],
                                env=env,
                                check=True,
                                capture_output=True,
                                text=True)
        assert f'flag=<{expected_flag}>' in result.stdout
        assert ('env=<CUDA_VISIBLE_DEVICES=4,5 '
                'LD_LIBRARY_PATH=/opt/accelerator/lib>' in result.stdout)
        assert 'SLURM_JOB_ID' not in result.stdout


class TestFormatSlurmDuration:
    """Test format_slurm_duration()."""

    @pytest.mark.parametrize('duration_seconds,expected', [
        (10000, '0-02:46:40'),
        (100000, '1-03:46:40'),
        (1000000, '11-13:46:40'),
        (None, 'UNLIMITED'),
    ])
    def test_format_slurm_duration(self, duration_seconds, expected):
        """Test format_slurm_duration with various inputs."""
        result = utils.format_slurm_duration(duration_seconds)
        assert result == expected


class TestValidateSbatchTime:
    """Test validate_sbatch_time()."""

    @pytest.mark.parametrize(
        'value',
        [
            '5',  # m (bare minutes)
            '1:30',  # m:s
            '4:00:00',  # h:m:s
            '1-0',  # d-h
            '1-12',  # d-h (multi-digit hour)
            '2-23:59',  # d-h:m
            '7-00:00:00',  # d-h:m:s
        ])
    def test_accepted_formats(self, value):
        # Should not raise. One sample per grammatical form.
        utils.validate_sbatch_time(value)

    @pytest.mark.parametrize('value', [
        '',
        'garbage',
        '1h',
        '1m30s',
        '1:2:3:4',
        '1.5',
        '-1',
        '1-2-3',
        ':30',
        '1:',
        ' 5',
        '5 ',
        '5\n',
    ])
    def test_invalid_formats_raise(self, value):
        with pytest.raises(ValueError, match='Invalid slurm.sbatch_options'):
            utils.validate_sbatch_time(value)


class TestGetIdentityFile:
    """Test get_identity_file() helper function."""

    @pytest.mark.parametrize(
        'ssh_config_dict,expected',
        [
            # Returns first file when multiple identity files are present
            ({
                'identityfile': ['/path/to/key1', '/path/to/key2']
            }, '/path/to/key1'),
            # Returns single identity file
            ({
                'identityfile': ['/home/user/.ssh/id_rsa']
            }, '/home/user/.ssh/id_rsa'),
            # Returns None when identityfile key is missing
            ({
                'hostname': 'example.com',
                'user': 'testuser'
            }, None),
            # Returns None when identityfile is an empty list
            ({
                'identityfile': []
            }, None),
            # Returns None when identityfile value is None
            ({
                'identityfile': None
            }, None),
        ])
    def test_get_identity_file(self, ssh_config_dict, expected):
        """Test get_identity_file with various SSH config inputs."""
        result = utils.get_identity_file(ssh_config_dict)
        assert result == expected


class TestGetSlurmNodesInfo:
    """Test user-scoped Slurm node discovery."""

    @pytest.mark.parametrize('slurm_user,expected_command_user',
                             [('alice', 'alice'), ('bob', 'bob'),
                              (None, 'transport-user')])
    def test_cache_and_query_are_scoped_by_command_user(self, monkeypatch,
                                                        slurm_user,
                                                        expected_command_user):
        ssh_config = mock.Mock()
        ssh_config.lookup.return_value = {
            'hostname': 'login.example.com',
            'user': 'transport-user',
        }
        monkeypatch.setattr(utils, 'get_slurm_ssh_config',
                            mock.Mock(return_value=ssh_config))
        monkeypatch.setattr(utils, 'get_submit_user',
                            mock.Mock(return_value=slurm_user))
        get_cache_entry = mock.Mock(return_value=None)
        monkeypatch.setattr(utils.kv_cache, 'get_cache_entry', get_cache_entry)
        monkeypatch.setattr(utils.kv_cache, 'add_or_update_cache_entry',
                            mock.Mock())
        client = mock.Mock()
        client.info_nodes.return_value = []
        slurm_client_cls = mock.Mock(return_value=client)
        monkeypatch.setattr(utils.slurm, 'SlurmClient', slurm_client_cls)

        assert utils.get_slurm_nodes_info('cluster-a') == []

        get_cache_entry.assert_called_once_with(
            f'slurm:nodes_info:cluster-a:{expected_command_user}')
        assert slurm_client_cls.call_args.kwargs['slurm_user'] == slurm_user


class TestClusterFeatureCache:
    """Test user-scoped Slurm feature checks."""

    @pytest.mark.parametrize('slurm_user,expected_command_user',
                             [('alice', 'alice'), ('bob', 'bob'),
                              (None, 'transport-user')])
    def test_cache_and_query_are_scoped_by_command_user(self, monkeypatch,
                                                        slurm_user,
                                                        expected_command_user):
        ssh_config = mock.Mock()
        ssh_config.lookup.return_value = {
            'hostname': 'login.example.com',
            'user': 'transport-user',
        }
        monkeypatch.setattr(utils, 'get_slurm_ssh_config',
                            mock.Mock(return_value=ssh_config))
        monkeypatch.setattr(utils, 'get_submit_user',
                            mock.Mock(return_value=slurm_user))
        get_cache_entry = mock.Mock(return_value=None)
        monkeypatch.setattr(utils.kv_cache, 'get_cache_entry', get_cache_entry)
        monkeypatch.setattr(utils.kv_cache, 'add_or_update_cache_entry',
                            mock.Mock())
        slurm_client_cls = mock.Mock(return_value=mock.Mock())
        monkeypatch.setattr(utils.slurm, 'SlurmClient', slurm_client_cls)

        assert utils._check_cluster_feature('cluster-a', 'fuse', lambda _: True,
                                            60)

        get_cache_entry.assert_called_once_with(
            f'slurm:fuse_enabled:cluster-a:{expected_command_user}')
        assert slurm_client_cls.call_args.kwargs['slurm_user'] == slurm_user


class TestGetPartitionInfos:
    """Test user-scoped Slurm partition caching."""

    def test_cache_is_scoped_by_submit_user(self, monkeypatch):
        utils._get_partition_infos.cache_clear()
        ssh_config = mock.Mock()
        ssh_config.lookup.return_value = {
            'hostname': 'login.example.com',
            'user': 'transport-user',
        }
        monkeypatch.setattr(utils.SSHConfig, 'from_path',
                            mock.Mock(return_value=ssh_config))
        submit_users = iter(['alice', 'bob', 'alice'])
        monkeypatch.setattr(utils, 'get_submit_user',
                            mock.Mock(side_effect=lambda _: next(submit_users)))

        def _make_client(*args, **kwargs):
            del args
            slurm_user = kwargs['slurm_user']
            client = mock.Mock()
            client.get_partitions_info.return_value = [
                utils.slurm.SlurmPartition(f'{slurm_user}-partition', True,
                                           None, None)
            ]
            return client

        slurm_client_cls = mock.Mock(side_effect=_make_client)
        monkeypatch.setattr(utils.slurm, 'SlurmClient', slurm_client_cls)

        try:
            alice = utils.get_partition_infos('cluster-a')
            bob = utils.get_partition_infos('cluster-a')
            alice_again = utils.get_partition_infos('cluster-a')

            assert set(alice) == {'alice-partition'}
            assert set(bob) == {'bob-partition'}
            assert alice_again is alice
            assert slurm_client_cls.call_count == 2
        finally:
            utils._get_partition_infos.cache_clear()


def _make_node_info(node_name: str, cluster_name: str) -> dict:
    return {
        'node_name': node_name,
        'slurm_cluster_name': cluster_name,
        'partition': 'main',
        'node_state': 'idle',
        'gpu_type': 'H100',
        'total_gpus': 8,
        'free_gpus': 8,
        'vcpu_count': 96,
        'memory_gb': 1024.0,
    }


def _make_command_error() -> exceptions.CommandError:
    """The error raised when a Slurm command fails, e.g. on an unreachable
    login node: `sinfo` exits with 255 and `handle_returncode` raises."""
    return exceptions.CommandError(
        255, 'sinfo -h --Node', 'Failed to get Slurm node information.',
        'ssh: connect to host slurm-login port 22: Connection timed out')


class TestSlurmNodeInfo:
    """Test slurm_node_info() multi-cluster aggregation."""

    def test_aggregates_all_clusters_when_no_name_given(self, monkeypatch):
        """slurm_node_info(None) should return nodes from all clusters."""
        clusters = ['cluster-a', 'cluster-b', 'cluster-c']
        monkeypatch.setattr('sky.clouds.Slurm.existing_allowed_clusters',
                            mock.Mock(return_value=clusters))
        monkeypatch.setattr(
            utils, '_get_slurm_node_info_list', lambda slurm_cluster_name: [
                _make_node_info(f'{slurm_cluster_name}-node-0',
                                slurm_cluster_name)
            ])

        result = utils.slurm_node_info()

        assert len(result) == 3
        assert {info['slurm_cluster_name'] for info in result} == set(clusters)

    def test_queries_only_specified_cluster(self, monkeypatch):
        """slurm_node_info with a name should query only that cluster."""
        existing_allowed_clusters = mock.Mock(
            return_value=['cluster-a', 'cluster-b'])
        monkeypatch.setattr('sky.clouds.Slurm.existing_allowed_clusters',
                            existing_allowed_clusters)
        get_node_info_list = mock.Mock(
            return_value=[_make_node_info('node-0', 'cluster-b')])
        monkeypatch.setattr(utils, '_get_slurm_node_info_list',
                            get_node_info_list)

        result = utils.slurm_node_info(slurm_cluster_name='cluster-b')

        assert len(result) == 1
        assert result[0]['slurm_cluster_name'] == 'cluster-b'
        get_node_info_list.assert_called_once_with(
            slurm_cluster_name='cluster-b')
        existing_allowed_clusters.assert_not_called()

    def test_returns_empty_list_when_no_clusters(self, monkeypatch):
        monkeypatch.setattr('sky.clouds.Slurm.existing_allowed_clusters',
                            mock.Mock(return_value=[]))
        assert not utils.slurm_node_info()

    @pytest.mark.parametrize('error', [
        RuntimeError('sinfo returned unexpected output'),
        _make_command_error(),
        KeyError('user'),
        exceptions.NotSupportedError('nope'),
        FileNotFoundError('~/.slurm/config'),
    ])
    def test_unreachable_cluster_does_not_break_others(self, monkeypatch,
                                                       error):
        """A failure on one cluster should not drop nodes of other clusters."""
        clusters = ['cluster-a', 'cluster-b']
        monkeypatch.setattr('sky.clouds.Slurm.existing_allowed_clusters',
                            mock.Mock(return_value=clusters))

        def _fake_get_node_info_list(slurm_cluster_name):
            if slurm_cluster_name == 'cluster-a':
                raise error
            return [_make_node_info('node-0', slurm_cluster_name)]

        monkeypatch.setattr(utils, '_get_slurm_node_info_list',
                            _fake_get_node_info_list)

        result = utils.slurm_node_info()

        assert len(result) == 1
        assert result[0]['slurm_cluster_name'] == 'cluster-b'

    def test_only_configured_cluster_failing_returns_empty_list(
            self, monkeypatch):
        """Aggregation is best-effort even with a single allowed cluster."""
        monkeypatch.setattr('sky.clouds.Slurm.existing_allowed_clusters',
                            mock.Mock(return_value=['cluster-a']))
        monkeypatch.setattr(utils, '_get_slurm_node_info_list',
                            mock.Mock(side_effect=_make_command_error()))
        assert not utils.slurm_node_info()

    def test_single_cluster_error_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(
            utils, '_get_slurm_node_info_list',
            mock.Mock(side_effect=exceptions.NotSupportedError('nope')))
        assert not utils.slurm_node_info(slurm_cluster_name='cluster-a')

    def test_single_cluster_command_error_propagates(self, monkeypatch):
        """An explicitly requested cluster surfaces query failures.

        Callers such as `core.realtime_slurm_gpu_availability` need to tell an
        unreachable cluster apart from a cluster without nodes.
        """
        monkeypatch.setattr(utils, '_get_slurm_node_info_list',
                            mock.Mock(side_effect=_make_command_error()))
        with pytest.raises(exceptions.CommandError):
            utils.slurm_node_info(slurm_cluster_name='cluster-a')


class TestSlurmClusterNames:
    """Test slurm_cluster_names()."""

    def test_returns_configured_clusters(self, monkeypatch):
        monkeypatch.setattr('sky.clouds.Slurm.existing_allowed_clusters',
                            mock.Mock(return_value=['cluster-a', 'cluster-b']))
        assert utils.slurm_cluster_names() == ['cluster-a', 'cluster-b']

    def test_returns_empty_list_when_none_configured(self, monkeypatch):
        monkeypatch.setattr('sky.clouds.Slurm.existing_allowed_clusters',
                            mock.Mock(return_value=[]))
        assert utils.slurm_cluster_names() == []

    def test_does_not_query_the_clusters(self, monkeypatch):
        """The point of the call: names without contacting a login node."""
        monkeypatch.setattr('sky.clouds.Slurm.existing_allowed_clusters',
                            mock.Mock(return_value=['cluster-a']))
        get_node_info_list = mock.Mock(
            side_effect=AssertionError('must not query the cluster'))
        monkeypatch.setattr(utils, '_get_slurm_node_info_list',
                            get_node_info_list)

        assert utils.slurm_cluster_names() == ['cluster-a']
        get_node_info_list.assert_not_called()


class TestGetGpuTypeAndCount:
    """Test get_gpu_type_and_count() GRES parsing."""

    @pytest.mark.parametrize(
        'gres_str,expected',
        [
            # Colon-style GRES from sinfo %G / squeue %b.
            ('gpu:8', (None, 8)),
            ('gpu:H100:8', ('H100', 8)),
            ('gpu:nvidia_h100_80gb_hbm3:8(S:0-1)',
             ('nvidia_h100_80gb_hbm3', 8)),
            # TRES/equals-style GRES that some Slurm versions return for
            # squeue %b. See #10283.
            ('gres/gpu=1', (None, 1)),
            ('gres/gpu:8', (None, 8)),
            ('gres/gpu:h100=2', ('h100', 2)),
            ('gres/gpu:h100:4', ('h100', 4)),
            # No GPU allocation.
            ('N/A', (None, 0)),
            ('(null)', (None, 0)),
            ('', (None, 0)),
            ('cpu=8,mem=32G', (None, 0)),
        ])
    def test_get_gpu_type_and_count(self, gres_str, expected):
        assert utils.get_gpu_type_and_count(gres_str) == expected


class TestGetGpuCountFromTres:
    """Test get_gpu_count_from_tres() TRES parsing."""

    @pytest.mark.parametrize(
        'tres_str,expected',
        [
            ('cpu=64,mem=800G,node=1,billing=64,gres/gpu=8', 8),
            # Untyped total is preferred over typed entries.
            ('cpu=64,mem=800G,gres/gpu=8,gres/gpu:h100=8', 8),
            ('gres/gpu:h100=8,gres/gpu=8', 8),
            # Only typed entries: counts are summed.
            ('cpu=8,gres/gpu:h100=4,gres/gpu:a100=2', 6),
            # No GPU entries.
            ('cpu=64,mem=400G', 0),
            ('', 0),
            # 'gres/gpu' should not match other gres types.
            ('cpu=8,gres/shard=32', 0),
        ])
    def test_get_gpu_count_from_tres(self, tres_str, expected):
        assert utils.get_gpu_count_from_tres(tres_str) == expected


class _FakeSlurmClient:
    """Fake SlurmClient for _get_slurm_node_info_list tests."""

    def __init__(self,
                 node_infos,
                 node_details=None,
                 jobs_gres=None,
                 details_error=None):
        self._node_infos = node_infos
        self._node_details = node_details or {}
        self._jobs_gres = jobs_gres or {}
        self._details_error = details_error
        self.jobs_gres_calls = 0

    def info_nodes(self):
        return self._node_infos

    def get_all_node_details(self):
        if self._details_error is not None:
            raise self._details_error
        return self._node_details

    def get_all_jobs_gres(self):
        self.jobs_gres_calls += 1
        return self._jobs_gres


class TestGetSlurmNodeInfoList:
    """Test _get_slurm_node_info_list() GPU accounting."""

    def _patch(self, monkeypatch, fake_client):
        ssh_config = mock.Mock()
        ssh_config.lookup.return_value = {
            'hostname': 'host',
            'user': 'user',
        }
        monkeypatch.setattr(utils, 'get_slurm_ssh_config', lambda: ssh_config)
        monkeypatch.setattr(utils.slurm, 'SlurmClient',
                            lambda *args, **kwargs: fake_client)

    def _make_sinfo_node(self, state) -> slurm_adaptor.NodeInfo:
        return slurm_adaptor.NodeInfo(node='node1',
                                      state=state,
                                      gres='gpu:h100:8',
                                      cpus=192,
                                      memory_gb=800.0,
                                      partition='main')

    def test_alloc_tres_used_when_squeue_gres_is_na(self, monkeypatch):
        """Node-level AllocTRES should be used even if squeue %b is N/A.

        Regression test for #10283: jobs requesting GPUs via
        --tres-per-task report %b as N/A, so squeue-based accounting
        counts 0 allocated GPUs.
        """
        fake_client = _FakeSlurmClient(
            node_infos=[self._make_sinfo_node('mix')],
            node_details={
                'node1': {
                    'CfgTRES': 'cpu=192,mem=800G,billing=192,gres/gpu=8',
                    'AllocTRES': 'cpu=64,mem=400G,gres/gpu=8',
                },
            },
            # squeue %b returned N/A for the job, so no GRES was recorded.
            jobs_gres={})
        self._patch(monkeypatch, fake_client)

        result = utils._get_slurm_node_info_list('cluster-a')

        assert len(result) == 1
        assert result[0]['total_gpus'] == 8
        assert result[0]['free_gpus'] == 0

    def test_alloc_tres_empty_on_idle_node(self, monkeypatch):
        """An idle node with empty AllocTRES should have all GPUs free."""
        fake_client = _FakeSlurmClient(
            node_infos=[self._make_sinfo_node('idle')],
            node_details={
                'node1': {
                    'CfgTRES': 'cpu=192,mem=800G,billing=192,gres/gpu=8',
                    'AllocTRES': '',
                },
            })
        self._patch(monkeypatch, fake_client)

        result = utils._get_slurm_node_info_list('cluster-a')

        assert len(result) == 1
        assert result[0]['free_gpus'] == 8

    def test_fallback_when_tres_lacks_gpu(self, monkeypatch):
        """Fall back to squeue if the cluster does not track gres/gpu in TRES.

        scontrol succeeds, but CfgTRES reports no GPUs while sinfo %G
        reports 8: AllocTRES cannot be trusted for GPU accounting, so the
        squeue-based path must be used.
        """
        fake_client = _FakeSlurmClient(
            node_infos=[self._make_sinfo_node('alloc')],
            node_details={
                'node1': {
                    'CfgTRES': 'cpu=192,mem=800G,billing=192',
                    'AllocTRES': 'cpu=192,mem=800G,billing=192',
                },
            },
            jobs_gres={'node1': ['gpu:h100:8']})
        self._patch(monkeypatch, fake_client)

        result = utils._get_slurm_node_info_list('cluster-a')

        assert len(result) == 1
        assert result[0]['free_gpus'] == 0

    def test_fallback_when_node_missing_from_details(self, monkeypatch):
        """A sinfo node absent from scontrol output uses squeue accounting."""
        fake_client = _FakeSlurmClient(
            node_infos=[self._make_sinfo_node('mix')],
            # scontrol succeeded but did not report this node (e.g. node
            # added between the two calls).
            node_details={},
            jobs_gres={'node1': ['gres/gpu=1']})
        self._patch(monkeypatch, fake_client)

        result = utils._get_slurm_node_info_list('cluster-a')

        assert len(result) == 1
        assert result[0]['free_gpus'] == 7

    def test_fallback_to_squeue_when_scontrol_fails(self, monkeypatch):
        """If scontrol fails, fall back to squeue-based accounting."""
        fake_client = _FakeSlurmClient(
            node_infos=[self._make_sinfo_node('mix')],
            details_error=RuntimeError('scontrol failed'),
            # Equals-style GRES string (see #10283 example 2).
            jobs_gres={'node1': ['gres/gpu=1']})
        self._patch(monkeypatch, fake_client)

        result = utils._get_slurm_node_info_list('cluster-a')

        assert len(result) == 1
        assert result[0]['free_gpus'] == 7

    def test_fallback_fully_allocated_node_without_gres(self, monkeypatch):
        """Fallback: alloc node with no job GRES info counts 0 free GPUs."""
        fake_client = _FakeSlurmClient(
            node_infos=[self._make_sinfo_node('alloc')],
            details_error=RuntimeError('scontrol failed'),
            jobs_gres={})
        self._patch(monkeypatch, fake_client)

        result = utils._get_slurm_node_info_list('cluster-a')

        assert len(result) == 1
        assert result[0]['free_gpus'] == 0

    def test_squeue_not_queried_on_cpu_only_cluster(self, monkeypatch):
        """CPU-only clusters never pay the squeue round-trip."""
        cpu_node = slurm_adaptor.NodeInfo(node='node1',
                                          state='alloc',
                                          gres='(null)',
                                          cpus=8,
                                          memory_gb=32.0,
                                          partition='main')
        fake_client = _FakeSlurmClient(node_infos=[cpu_node])
        self._patch(monkeypatch, fake_client)

        result = utils._get_slurm_node_info_list('cluster-a')

        assert len(result) == 1
        assert result[0]['total_gpus'] == 0
        assert result[0]['free_gpus'] == 0
        assert fake_client.jobs_gres_calls == 0

    def test_squeue_not_queried_when_tres_covers_all_nodes(self, monkeypatch):
        """squeue is not queried when AllocTRES covers every GPU node."""
        fake_client = _FakeSlurmClient(
            node_infos=[self._make_sinfo_node('mix')],
            node_details={
                'node1': {
                    'CfgTRES': 'cpu=192,mem=800G,billing=192,gres/gpu=8',
                    'AllocTRES': 'cpu=64,mem=400G,gres/gpu=4',
                },
            })
        self._patch(monkeypatch, fake_client)

        result = utils._get_slurm_node_info_list('cluster-a')

        assert len(result) == 1
        assert result[0]['free_gpus'] == 4
        assert fake_client.jobs_gres_calls == 0


class TestGetSlurmNodeInfoListEnrichment:
    """Test scontrol-based CPU/memory enrichment in _get_slurm_node_info_list."""

    def _run(self, monkeypatch, node_details_result):
        from sky.adaptors import slurm as slurm_adaptor

        client = mock.Mock()
        client.info_nodes.return_value = [
            slurm_adaptor.NodeInfo(node='node1',
                                   state='mix',
                                   gres='gpu:gh200:1',
                                   cpus=72,
                                   memory_gb=420.0,
                                   partition='all*'),
        ]
        client.get_all_jobs_gres.return_value = {}
        if isinstance(node_details_result, Exception):
            client.get_all_node_details.side_effect = node_details_result
        else:
            client.get_all_node_details.return_value = node_details_result

        ssh_config = mock.Mock()
        ssh_config.lookup.return_value = {
            'hostname': 'login.example.com',
            'user': 'me',
        }
        monkeypatch.setattr(utils, 'get_slurm_ssh_config', lambda: ssh_config)
        slurm_client_cls = mock.Mock(return_value=client)
        monkeypatch.setattr(utils.slurm, 'SlurmClient', slurm_client_cls)
        result = utils._get_slurm_node_info_list('cluster-a')
        return result, slurm_client_cls

    def test_uses_transport_user(self, monkeypatch):
        get_submit_user = mock.Mock(side_effect=AssertionError)
        monkeypatch.setattr(utils, 'get_submit_user', get_submit_user)

        _, slurm_client_cls = self._run(monkeypatch, {})

        get_submit_user.assert_not_called()
        assert slurm_client_cls.call_args.kwargs['slurm_user'] is None

    def test_enriches_from_scontrol(self, monkeypatch):
        result, _ = self._run(
            monkeypatch, {
                'node1': {
                    'CPUAlloc': '8',
                    'CPUTot': '72',
                    'CPULoad': '3.50',
                    'RealMemory': '430080',
                    'AllocMem': '102400',
                    'FreeMem': '421339',
                },
            })
        assert len(result) == 1
        node = result[0]
        assert node['free_vcpus'] == 64
        assert node['free_alloc_memory_gb'] == round((430080 - 102400) / 1024.0,
                                                     2)
        assert node['cpu_load'] == 3.5
        assert node['free_memory_gb'] == round(421339 / 1024.0, 2)

    def test_na_values_become_none(self, monkeypatch):
        result, _ = self._run(
            monkeypatch, {
                'node1': {
                    'CPUAlloc': '0',
                    'CPUTot': '72',
                    'CPULoad': 'N/A',
                    'RealMemory': '430080',
                    'AllocMem': '0',
                    'FreeMem': 'N/A',
                },
            })
        node = result[0]
        assert node['free_vcpus'] == 72
        assert node['free_alloc_memory_gb'] == round(430080 / 1024.0, 2)
        assert node['cpu_load'] is None
        assert node['free_memory_gb'] is None

    def test_missing_node_details_yields_none_fields(self, monkeypatch):
        result, _ = self._run(monkeypatch, {})
        node = result[0]
        assert node['free_vcpus'] is None
        assert node['free_alloc_memory_gb'] is None
        assert node['cpu_load'] is None
        assert node['free_memory_gb'] is None

    def test_scontrol_failure_does_not_break_node_info(self, monkeypatch):
        result, _ = self._run(monkeypatch, RuntimeError('scontrol failed'))
        assert len(result) == 1
        node = result[0]
        assert node['node_name'] == 'node1'
        assert node['free_vcpus'] is None
        assert node['cpu_load'] is None
