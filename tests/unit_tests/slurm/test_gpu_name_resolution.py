"""Tests for Slurm GPU name canonicalization and resolution."""

from unittest import mock

import pytest

from sky.adaptors.slurm import NodeInfo
from sky.exceptions import ResourcesUnavailableError
from sky.provision.slurm.utils import _accelerator_name_matches_slurm
from sky.provision.slurm.utils import _normalize_gpu_name
from sky.provision.slurm.utils import canonicalize_raw_gpu_name
from sky.provision.slurm.utils import check_instance_fits
from sky.provision.slurm.utils import resolve_gres_gpu_type


# ---------------------------------------------------------------------------
# _normalize_gpu_name
# ---------------------------------------------------------------------------
class TestNormalizeGpuName:
    """Tests for _normalize_gpu_name."""

    def test_basic_lowering(self):
        assert _normalize_gpu_name('H100') == 'h100'

    def test_underscores_to_dashes(self):
        assert _normalize_gpu_name('H100_80GB') == 'h100-80gb'

    def test_nvidia_prefix_stripped(self):
        assert _normalize_gpu_name('NVIDIA_H100_80GB_S') == 'h100-80gb-s'

    def test_tesla_prefix_stripped(self):
        assert _normalize_gpu_name('TESLA_V100_SXM2') == 'v100-sxm2'

    def test_amd_prefix_stripped(self):
        assert _normalize_gpu_name('AMD_MI250X') == 'mi250x'

    def test_no_prefix_no_change(self):
        assert _normalize_gpu_name('A100-SXM-80GB') == 'a100-sxm-80gb'

    def test_nvidia_embedded_not_stripped(self):
        # 'nvidia' that is not a prefix should not be stripped.
        assert _normalize_gpu_name('foNVIDIA_bar') == 'fonvidia-bar'

    def test_empty_string(self):
        assert _normalize_gpu_name('') == ''


# ---------------------------------------------------------------------------
# _accelerator_name_matches_slurm
# ---------------------------------------------------------------------------
class TestAcceleratorNameMatchesSlurm:
    """Tests for _accelerator_name_matches_slurm."""

    # --- Exact case-insensitive match ---
    def test_exact_same_case(self):
        assert _accelerator_name_matches_slurm('H100', 'H100')

    def test_exact_different_case(self):
        assert _accelerator_name_matches_slurm('h100', 'H100')

    def test_exact_raw_match(self):
        assert _accelerator_name_matches_slurm('nvidia_h100_80gb_hbm3',
                                               'nvidia_h100_80gb_hbm3')

    # --- Normalized equality ---
    def test_canonical_vs_raw_nvidia_prefix(self):
        # 'H100-80GB-S' normalizes to 'h100-80gb-s'
        # 'NVIDIA_H100_80GB_S' also normalizes to 'h100-80gb-s'
        assert _accelerator_name_matches_slurm('H100-80GB-S',
                                               'NVIDIA_H100_80GB_S')

    def test_canonical_vs_raw_tesla_prefix(self):
        assert _accelerator_name_matches_slurm('V100-SXM2', 'TESLA_V100_SXM2')

    # --- Prefix match ---
    def test_canonical_short_prefix(self):
        # 'H100' should match 'NVIDIA_H100_80GB_S' via prefix after
        # normalization: 'h100' is prefix of 'h100-80gb-s' with '-'.
        assert _accelerator_name_matches_slurm('H100', 'NVIDIA_H100_80GB_S')

    def test_canonical_prefix_with_suffix(self):
        assert _accelerator_name_matches_slurm('H100-80GB',
                                               'NVIDIA_H100_80GB_S')

    def test_prefix_match_a100(self):
        assert _accelerator_name_matches_slurm('A100', 'A100-SXM-80GB')

    def test_prefix_match_reverse(self):
        # The raw type is shorter; requested is longer.
        assert _accelerator_name_matches_slurm('H100-80GB-S', 'H100')

    # --- False positives that must NOT match ---
    def test_l4_does_not_match_l40(self):
        assert not _accelerator_name_matches_slurm('L4', 'L40')

    def test_l4_does_not_match_l40s(self):
        assert not _accelerator_name_matches_slurm('L4', 'L40S')

    def test_a10_does_not_match_a100(self):
        assert not _accelerator_name_matches_slurm('A10', 'A100')

    def test_v100_does_not_match_v10(self):
        assert not _accelerator_name_matches_slurm('V100', 'V10')

    def test_completely_different(self):
        assert not _accelerator_name_matches_slurm('H100', 'A100')

    def test_h200_does_not_match_h100(self):
        assert not _accelerator_name_matches_slurm('H200', 'H100')

    # --- Memory-variant (non-contiguous segments) ---
    def test_a100_80gb_matches_sxm4_variant(self):
        assert _accelerator_name_matches_slurm('A100-80GB',
                                               'nvidia_a100_sxm4_80gb')

    def test_a100_80gb_matches_sxm_variant(self):
        assert _accelerator_name_matches_slurm('A100-80GB',
                                               'NVIDIA_A100_SXM_80GB')

    def test_v100_32gb_matches_pcie_variant(self):
        assert _accelerator_name_matches_slurm('V100-32GB',
                                               'tesla_v100_pcie_32gb')

    def test_v100_32gb_does_not_match_16gb(self):
        assert not _accelerator_name_matches_slurm('V100-32GB',
                                                   'tesla_v100_pcie_16gb')

    def test_a100_80gb_does_not_match_40gb(self):
        assert not _accelerator_name_matches_slurm('A100-80GB',
                                                   'nvidia_a100_pcie_40gb')


# ---------------------------------------------------------------------------
# Helpers for building mock NodeInfo lists
# ---------------------------------------------------------------------------
def _node(name: str,
          gres: str,
          partition: str = 'gpu',
          cpus: int = 64,
          memory_gb: float = 512.0) -> NodeInfo:
    return NodeInfo(node=name,
                    state='idle',
                    gres=gres,
                    cpus=cpus,
                    memory_gb=memory_gb,
                    partition=partition)


# ---------------------------------------------------------------------------
# resolve_gres_gpu_type
# ---------------------------------------------------------------------------
class TestResolveGresGpuType:  # pylint: disable=unused-argument
    """Tests for resolve_gres_gpu_type."""

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_canonical_h100_resolves_to_raw(self, mock_nodes, mock_part):
        mock_nodes.return_value = [
            _node('node1', 'gpu:NVIDIA_H100_80GB_S:8'),
            _node('node2', 'gpu:NVIDIA_H100_80GB_S:8'),
        ]
        result = resolve_gres_gpu_type('cluster1', 'H100', 8)
        assert result == 'NVIDIA_H100_80GB_S'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_canonical_h100_80gb_resolves(self, mock_nodes, mock_part):
        mock_nodes.return_value = [
            _node('node1', 'gpu:NVIDIA_H100_80GB_S:8'),
        ]
        result = resolve_gres_gpu_type('cluster1', 'H100-80GB', 1)
        assert result == 'NVIDIA_H100_80GB_S'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_exact_raw_request_returns_itself(self, mock_nodes, mock_part):
        mock_nodes.return_value = [
            _node('node1', 'gpu:NVIDIA_H100_80GB_S:8'),
        ]
        result = resolve_gres_gpu_type('cluster1', 'NVIDIA_H100_80GB_S', 1)
        assert result == 'NVIDIA_H100_80GB_S'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_case_insensitive_request(self, mock_nodes, mock_part):
        mock_nodes.return_value = [
            _node('node1', 'gpu:h100:4'),
        ]
        result = resolve_gres_gpu_type('cluster1', 'H100', 4)
        assert result == 'h100'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_partition_scoped(self, mock_nodes, mock_part):
        mock_nodes.return_value = [
            _node('node1', 'gpu:A100:8', partition='train'),
            _node('node2', 'gpu:H100:8', partition='infer'),
        ]
        result = resolve_gres_gpu_type('cluster1', 'H100', 8, partition='infer')
        assert result == 'H100'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_partition_scoped_excludes_other(self, mock_nodes, mock_part):
        mock_nodes.return_value = [
            _node('node1', 'gpu:H100:8', partition='train'),
        ]
        with pytest.raises(ResourcesUnavailableError):
            resolve_gres_gpu_type('cluster1', 'H100', 8, partition='infer')

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_no_match_raises_with_discovered_types(self, mock_nodes, mock_part):
        mock_nodes.return_value = [
            _node('node1', 'gpu:A100:8'),
            _node('node2', 'gpu:V100:4'),
        ]
        with pytest.raises(ResourcesUnavailableError, match='A100'):
            resolve_gres_gpu_type('cluster1', 'H100', 1)

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_count_filter(self, mock_nodes, mock_part):
        """Nodes with fewer GPUs than requested are excluded."""
        mock_nodes.return_value = [
            _node('node1', 'gpu:H100:4'),
            _node('node2', 'gpu:H100:8'),
        ]
        # Requesting 8 should still succeed via node2.
        result = resolve_gres_gpu_type('cluster1', 'H100', 8)
        assert result == 'H100'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_count_filter_excludes_all(self, mock_nodes, mock_part):
        """All nodes have fewer GPUs than requested."""
        mock_nodes.return_value = [
            _node('node1', 'gpu:H100:4'),
        ]
        with pytest.raises(ResourcesUnavailableError):
            resolve_gres_gpu_type('cluster1', 'H100', 8)

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_deterministic_ambiguity_prefers_exact(self, mock_nodes, mock_part):
        """When multiple raw types match, exact case-insensitive wins."""
        mock_nodes.return_value = [
            _node('node1', 'gpu:NVIDIA_H100_80GB_S:8'),
            _node('node2', 'gpu:H100:8'),
        ]
        result = resolve_gres_gpu_type('cluster1', 'H100', 1)
        # 'H100' is an exact match, so it should be preferred over the longer
        # NVIDIA_H100_80GB_S even though both are compatible.
        assert result == 'H100'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_deterministic_ambiguity_prefers_more_nodes(self, mock_nodes,
                                                        mock_part):
        """When no exact match, prefer the type with more supporting nodes."""
        mock_nodes.return_value = [
            _node('node1', 'gpu:NVIDIA_H100_80GB:8'),
            _node('node2', 'gpu:NVIDIA_H100_80GB_S:8'),
            _node('node3', 'gpu:NVIDIA_H100_80GB_S:8'),
        ]
        result = resolve_gres_gpu_type('cluster1', 'H100', 1)
        assert result == 'NVIDIA_H100_80GB_S'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_deterministic_ambiguity_lexicographic_tiebreak(
            self, mock_nodes, mock_part):
        """Equal node count: lexicographic tiebreak by raw type."""
        mock_nodes.return_value = [
            _node('node1', 'gpu:NVIDIA_H100_80GB_S:8'),
            _node('node2', 'gpu:NVIDIA_H100_80GB_B:8'),
        ]
        result = resolve_gres_gpu_type('cluster1', 'H100', 1)
        # Both have 1 node, so lexicographic wins: 'B' < 'S'.
        assert result == 'NVIDIA_H100_80GB_B'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_default_partition_star(self, mock_nodes, mock_part):
        """Default partition with '*' suffix is handled correctly."""
        mock_nodes.return_value = [
            _node('node1', 'gpu:H100:8', partition='gpu*'),
        ]
        result = resolve_gres_gpu_type('cluster1', 'H100', 8, partition='gpu')
        assert result == 'H100'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_l4_does_not_resolve_to_l40(self, mock_nodes, mock_part):
        """L4 must not match L40 or L40S."""
        mock_nodes.return_value = [
            _node('node1', 'gpu:L40:8'),
            _node('node2', 'gpu:L40S:8'),
        ]
        with pytest.raises(ResourcesUnavailableError):
            resolve_gres_gpu_type('cluster1', 'L4', 1)

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_no_gpu_nodes_at_all(self, mock_nodes, mock_part):
        mock_nodes.return_value = [
            _node('node1', '(null)', cpus=128, memory_gb=1024),
        ]
        with pytest.raises(ResourcesUnavailableError,
                           match='No GPU nodes found'):
            resolve_gres_gpu_type('cluster1', 'H100', 1)


# ---------------------------------------------------------------------------
# check_instance_fits with canonical matching
# ---------------------------------------------------------------------------
class TestCheckInstanceFitsCanonical:  # pylint: disable=unused-argument
    """Tests for check_instance_fits with canonical GPU name matching."""

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_canonical_h100_fits(self, mock_nodes, mock_part):
        """Canonical H100 request should fit on NVIDIA_H100_80GB_S nodes."""
        mock_nodes.return_value = [
            _node('node1',
                  'gpu:NVIDIA_H100_80GB_S:8',
                  partition='gpu',
                  cpus=64,
                  memory_gb=512),
        ]
        fits, reason = check_instance_fits('cluster1',
                                           '4CPU--16GB--H100:8',
                                           partition='gpu')
        assert fits, f'Expected fit but got: {reason}'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_exact_raw_still_fits(self, mock_nodes, mock_part):
        """Exact raw GRES name should still work."""
        mock_nodes.return_value = [
            _node('node1',
                  'gpu:NVIDIA_H100_80GB_S:8',
                  partition='gpu',
                  cpus=64,
                  memory_gb=512),
        ]
        fits, reason = check_instance_fits('cluster1',
                                           '4CPU--16GB--NVIDIA_H100_80GB_S:8',
                                           partition='gpu')
        assert fits, f'Expected fit but got: {reason}'

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_l4_does_not_fit_l40_nodes(self, mock_nodes, mock_part):
        """L4 request should NOT fit on L40 nodes."""
        mock_nodes.return_value = [
            _node('node1', 'gpu:L40:8', partition='gpu', cpus=64,
                  memory_gb=512),
        ]
        fits, _ = check_instance_fits('cluster1',
                                      '4CPU--16GB--L4:1',
                                      partition='gpu')
        assert not fits

    @mock.patch('sky.provision.slurm.utils.get_cluster_default_partition',
                return_value='gpu')
    @mock.patch('sky.provision.slurm.utils._get_slurm_nodes_info')
    def test_error_lists_available_types(self, mock_nodes, mock_part):
        """Error message should list discovered GPU types."""
        mock_nodes.return_value = [
            _node('node1',
                  'gpu:A100:8',
                  partition='gpu',
                  cpus=64,
                  memory_gb=512),
        ]
        fits, reason = check_instance_fits('cluster1',
                                           '4CPU--16GB--H100:1',
                                           partition='gpu')
        assert not fits
        assert 'A100' in reason


# ---------------------------------------------------------------------------
# canonicalize_raw_gpu_name
# ---------------------------------------------------------------------------
class TestCanonicalizeRawGpuName:
    """Tests for canonicalize_raw_gpu_name."""

    def test_nvidia_h100_80gb_hbm3(self):
        assert canonicalize_raw_gpu_name('nvidia_h100_80gb_hbm3') == 'H100-80GB'

    def test_nvidia_l40s(self):
        assert canonicalize_raw_gpu_name('nvidia_l40s') == 'L40S'

    def test_nvidia_a100_sxm4_80gb(self):
        assert canonicalize_raw_gpu_name('NVIDIA_A100_SXM4_80GB') == 'A100-80GB'

    def test_already_canonical_h100(self):
        assert canonicalize_raw_gpu_name('H100') == 'H100'

    def test_already_canonical_v100(self):
        assert canonicalize_raw_gpu_name('V100') == 'V100'

    def test_l40_not_l4(self):
        """L40 should canonicalize to L40, not L4."""
        assert canonicalize_raw_gpu_name('nvidia_l40') == 'L40'

    def test_l4_stays_l4(self):
        assert canonicalize_raw_gpu_name('nvidia_l4') == 'L4'

    def test_a100_40gb_maps_to_a100(self):
        """A 40GB A100 does not contain '80GB' so should match A100."""
        assert canonicalize_raw_gpu_name('NVIDIA_A100_PCIE_40GB') == 'A100'

    def test_tesla_v100(self):
        assert canonicalize_raw_gpu_name('TESLA_V100_SXM2') == 'V100'

    def test_unknown_falls_back_to_upper(self):
        assert canonicalize_raw_gpu_name('unknown_custom_gpu') == (
            'UNKNOWN_CUSTOM_GPU')

    def test_uppercase_raw_gres(self):
        """Uppercase raw GRES (e.g. from PR #8399 scenario)."""
        assert canonicalize_raw_gpu_name('NVIDIA_H100_80GB_S') == 'H100-80GB'

    def test_b200(self):
        assert canonicalize_raw_gpu_name('nvidia_b200') == 'B200'

    def test_a100_sxm4_80gb(self):
        """Non-contiguous memory variant: SXM4 between model and 80GB."""
        assert canonicalize_raw_gpu_name('nvidia_a100_sxm4_80gb') == 'A100-80GB'

    def test_tesla_v100_pcie_32gb(self):
        assert canonicalize_raw_gpu_name('tesla_v100_pcie_32gb') == 'V100-32GB'
