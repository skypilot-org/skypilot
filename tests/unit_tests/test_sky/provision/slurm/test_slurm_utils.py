"""Unit tests for sky.provision.slurm.utils."""
from unittest import mock

import pytest

from sky import exceptions
from sky.provision.slurm import utils


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

    def test_unreachable_cluster_does_not_break_others(self, monkeypatch):
        """A failure on one cluster should not drop nodes of other clusters."""
        clusters = ['cluster-a', 'cluster-b']
        monkeypatch.setattr('sky.clouds.Slurm.existing_allowed_clusters',
                            mock.Mock(return_value=clusters))

        def _fake_get_node_info_list(slurm_cluster_name):
            if slurm_cluster_name == 'cluster-a':
                raise RuntimeError('SSH connection failed')
            return [_make_node_info('node-0', slurm_cluster_name)]

        monkeypatch.setattr(utils, '_get_slurm_node_info_list',
                            _fake_get_node_info_list)

        result = utils.slurm_node_info()

        assert len(result) == 1
        assert result[0]['slurm_cluster_name'] == 'cluster-b'

    def test_single_cluster_error_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(
            utils, '_get_slurm_node_info_list',
            mock.Mock(side_effect=exceptions.NotSupportedError('nope')))
        assert not utils.slurm_node_info(slurm_cluster_name='cluster-a')


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
        monkeypatch.setattr(utils.slurm, 'SlurmClient',
                            mock.Mock(return_value=client))
        return utils._get_slurm_node_info_list('cluster-a')

    def test_enriches_from_scontrol(self, monkeypatch):
        result = self._run(
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
        result = self._run(
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
        result = self._run(monkeypatch, {})
        node = result[0]
        assert node['free_vcpus'] is None
        assert node['free_alloc_memory_gb'] is None
        assert node['cpu_load'] is None
        assert node['free_memory_gb'] is None

    def test_scontrol_failure_does_not_break_node_info(self, monkeypatch):
        result = self._run(monkeypatch, RuntimeError('scontrol failed'))
        assert len(result) == 1
        node = result[0]
        assert node['node_name'] == 'node1'
        assert node['free_vcpus'] is None
        assert node['cpu_load'] is None
