"""Tests for Slurm node index resolution."""

import pytest

from sky.skylet.executor import slurm


class TestGetNodeIdx:
    """Tests for _get_node_idx."""

    @pytest.mark.parametrize('slurm_node_id,expected', [('0', 0), ('1', 1),
                                                        ('2', 2)])
    def test_prefers_slurm_node_id(self, monkeypatch, slurm_node_id, expected):
        """SLURM_NODEID wins even when the runtime IP is not in cluster_ips."""
        monkeypatch.setenv('SLURM_NODEID', slurm_node_id)
        cluster_ips = ['10.244.10.126', '10.244.10.127', '10.244.10.128']
        assert slurm._get_node_idx(cluster_ips, '10.102.3.53') == expected

    def test_falls_back_to_ip_when_node_id_unset(self, monkeypatch):
        monkeypatch.delenv('SLURM_NODEID', raising=False)
        cluster_ips = ['10.0.0.1', '10.0.0.2']
        assert slurm._get_node_idx(cluster_ips, '10.0.0.2') == 1

    @pytest.mark.parametrize('slurm_node_id', ['2', '-1', 'not-an-int'])
    def test_falls_back_to_ip_when_node_id_unusable(self, monkeypatch,
                                                    slurm_node_id):
        """Out-of-range or non-integer SLURM_NODEID must not be trusted."""
        monkeypatch.setenv('SLURM_NODEID', slurm_node_id)
        cluster_ips = ['10.0.0.1', '10.0.0.2']
        assert slurm._get_node_idx(cluster_ips, '10.0.0.2') == 1

    def test_single_node_cluster_skips_ip_matching(self, monkeypatch):
        """A one-node cluster has only one possible index."""
        monkeypatch.delenv('SLURM_NODEID', raising=False)
        assert slurm._get_node_idx(['10.244.10.126'], '10.102.3.53') == 0

    def test_raises_when_ip_not_in_cluster_ips(self, monkeypatch):
        monkeypatch.delenv('SLURM_NODEID', raising=False)
        cluster_ips = ['10.244.10.126', '10.244.10.127']
        with pytest.raises(RuntimeError, match='not found in cluster IPs'):
            slurm._get_node_idx(cluster_ips, '10.102.3.53')
