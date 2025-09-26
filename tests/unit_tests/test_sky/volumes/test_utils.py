"""Unit tests for volume utils."""

from datetime import datetime
from unittest import mock

import pytest

from sky.volumes import utils
from sky.volumes import volume


class TestPVCVolumeTable:
    """Test cases for PVCVolumeTable."""

    def test_pvc_volume_table_init(self):
        """Test PVCVolumeTable initialization."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply'
        }]

        table = utils.PVCVolumeTable(volumes, show_all=False)
        assert table is not None
        assert hasattr(table, 'table')

    def test_pvc_volume_table_format_basic(self):
        """Test PVCVolumeTable formatting with basic columns."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply'
        }]

        table = utils.PVCVolumeTable(volumes, show_all=False)
        result = table.format()

        assert isinstance(result, str)
        assert 'test-volume-1' in result
        assert 'k8s-pvc' in result
        assert '100Gi' in result

    def test_pvc_volume_table_format_show_all(self):
        """Test PVCVolumeTable formatting with show_all=True."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default',
                'storage_class_name': 'gp2',
                'access_mode': 'ReadWriteOnce'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply',
            'name_on_cloud': 'test-volume-1-abc123'
        }]

        table = utils.PVCVolumeTable(volumes, show_all=True)
        result = table.format()

        assert isinstance(result, str)
        assert 'test-volume-1' in result
        assert 'test-volume-1-abc123' in result
        assert 'gp2' in result
        assert 'ReadWriteOnce' in result

    def test_pvc_volume_table_empty_volumes(self):
        """Test PVCVolumeTable with empty volumes list."""
        volumes = []

        table = utils.PVCVolumeTable(volumes, show_all=False)
        result = table.format()

        # For empty volumes, the table returns an empty string
        assert result == 'Kubernetes PVCs:\n'

    def test_pvc_volume_table_null_values(self):
        """Test PVCVolumeTable with null/None values."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': None,
            'config': {},
            'size': None,
            'user_hash': '',
            'workspace': None,
            'launched_at': None,
            'last_attached_at': None,
            'status': None,
            'last_use': ''  # Use empty string instead of None to avoid truncate error
        }]

        table = utils.PVCVolumeTable(volumes, show_all=False)
        result = table.format()

        assert isinstance(result, str)
        assert 'test-volume-1' in result
        assert 'k8s-pvc' in result

    def test_pvc_volume_table_timestamp_conversion(self):
        """Test PVCVolumeTable timestamp conversion."""
        test_timestamp = 1234567890
        expected_time = datetime.fromtimestamp(test_timestamp).strftime(
            '%Y-%m-%d %H:%M:%S')

        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': test_timestamp,
            'last_attached_at': test_timestamp,
            'status': 'READY',
            'last_use': 'sky volumes apply'
        }]

        table = utils.PVCVolumeTable(volumes, show_all=False)
        result = table.format()

        assert expected_time in result


class TestFormatVolumeTable:
    """Test cases for format_volume_table function."""

    def test_format_volume_table_empty_list(self):
        """Test format_volume_table with empty volumes list."""
        volumes = []

        result = utils.format_volume_table(volumes, show_all=False)

        assert result == 'No existing volumes.'

    def test_format_volume_table_pvc_volumes(self):
        """Test format_volume_table with PVC volumes."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'usedby_pods': ['p1'],
            'last_use': 'sky volumes apply'
        }]

        result = utils.format_volume_table(volumes, show_all=False)

        assert isinstance(result, str)
        assert 'test-volume-1' in result
        assert 'k8s-pvc' in result
        assert 'p1' in result

    def test_format_volume_table_unknown_volume_type(self, monkeypatch):
        """Test format_volume_table with unknown volume type."""
        mock_logger = mock.MagicMock()
        monkeypatch.setattr(utils, 'logger', mock_logger)

        volumes = [{
            'name': 'test-volume-1',
            'type': 'unknown-type',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply'
        }]

        result = utils.format_volume_table(volumes, show_all=False)

        assert result == 'No existing volumes.'
        mock_logger.warning.assert_called_once_with(
            'Unknown volume type: unknown-type')

    def test_format_volume_table_show_all_true(self):
        """Test format_volume_table with show_all=True."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default',
                'storage_class_name': 'gp2',
                'access_mode': 'ReadWriteOnce'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply',
            'usedby_pods': ['p1'],
            'usedby_clusters': ['c1'],
            'name_on_cloud': 'test-volume-1-abc123'
        }]

        result = utils.format_volume_table(volumes, show_all=True)

        assert isinstance(result, str)
        assert 'test-volume-1' in result
        assert 'test-volume-1-abc123' in result
        assert 'gp2' in result
        assert 'ReadWriteOnce' in result
        assert 'c1' in result


class TestVolumeTableABC:
    """Test cases for VolumeTable abstract base class."""

    def test_volume_table_abc_instantiation(self):
        """Test that VolumeTable ABC cannot be instantiated directly."""
        with pytest.raises(TypeError):
            utils.VolumeTable()

    def test_pvc_volume_table_inheritance(self):
        """Test that PVCVolumeTable properly inherits from VolumeTable."""
        volumes = [{
            'name': 'test-volume-1',
            'type': 'k8s-pvc',
            'region': 'context-1',
            'config': {
                'namespace': 'default'
            },
            'size': '100',
            'user_hash': 'user123',
            'workspace': 'default',
            'launched_at': 1234567890,
            'last_attached_at': 1234567891,
            'status': 'READY',
            'last_use': 'sky volumes apply'
        }]

        table = utils.PVCVolumeTable(volumes, show_all=False)

        assert isinstance(table, utils.VolumeTable)
        assert hasattr(table, 'format')
        assert callable(table.format)


class TestRunPodVolumeTable:

    def test_runpod_volume_table_basic(self):
        from sky.volumes import utils as vutils
        volumes = [{
            'name': 'rpv-1',
            'type': 'runpod-network-volume',
            'cloud': 'runpod',
            'region': 'us',
            'zone': 'iad-1',
            'size': '100',
            'user_name': 'u',
            'workspace': 'w',
            'launched_at': 0,
            'last_attached_at': None,
            'status': 'READY',
            'usedby_clusters': ['c1', 'c2']
        }]
        table = vutils.RunPodVolumeTable(volumes, show_all=False)
        out = table.format()
        assert 'RunPod Network Volumes:' in out
        assert 'rpv-1' in out
        assert 'runpod/us/iad-1' in out
        assert '100Gi' in out
        assert 'c1, c2'[:vutils.constants.USED_BY_TRUNC_LENGTH] in out

    def test_runpod_volume_table_show_all(self):
        from sky.volumes import utils as vutils
        volumes = [{
            'name': 'rpv-2',
            'type': 'runpod-network-volume',
            'cloud': 'runpod',
            'region': None,
            'zone': 'iad-1',
            'size': '50',
            'user_name': 'u',
            'workspace': 'w',
            'launched_at': 0,
            'last_attached_at': 1234567890,
            'status': 'READY',
            'usedby_pods': ['p1'],
            'name_on_cloud': 'vol-abc'
        }]
        table = vutils.RunPodVolumeTable(volumes, show_all=True)
        out = table.format()
        assert 'RunPod Network Volumes:' in out
        assert 'rpv-2' in out
        assert '/iad-1' in out
        assert 'vol-abc' in out
        assert 'p1' in out

    def test_get_infra_str(self):
        from sky.client.cli.table_utils import _get_infra_str
        assert _get_infra_str(None, None, None) == ''
        assert _get_infra_str('runpod', None, None) == 'runpod'
        assert _get_infra_str('runpod', 'us', None) == 'runpod/us'
        assert _get_infra_str('runpod', 'us', 'iad-1') == 'runpod/us/iad-1'

    def test_format_volume_table_mixed_types_and_separator(self):
        from sky.client.cli.table_utils import format_volume_table
        volumes = [{
            'name': 'p1',
            'type': 'k8s-pvc',
            'cloud': 'kubernetes',
            'region': 'ctx',
            'zone': None,
            'size': '10'
        }, {
            'name': 'r1',
            'type': 'runpod-network-volume',
            'cloud': 'runpod',
            'region': 'us',
            'zone': 'iad-1',
            'size': '20'
        }]
        out = format_volume_table(volumes, show_all=False)
        # Both headers present and separated by blank line
        assert 'Kubernetes PVCs:' in out
        assert 'RunPod Network Volumes:' in out
        assert '\n\n' in out
