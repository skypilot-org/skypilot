from unittest.mock import patch

import pytest

from sky.serve import serve_utils


class TestValidateLogFile:

    @pytest.fixture
    def mock_exists(self):
        with patch('os.path.exists') as mock:
            yield mock

    def test_file_not_exists(self, mock_exists):
        # File does not exist, return False.
        mock_exists.return_value = False
        assert not serve_utils.validate_log_file('replica_1.log', 1)

    def test_target_id_none(self, mock_exists):
        # File exists, Target ID is None, always return True.
        mock_exists.return_value = True
        assert serve_utils.validate_log_file('replica_1.log', None)

    def test_valid_replica_id(self, mock_exists):
        # File exists, ReplicaID matches, return True.
        mock_exists.return_value = True
        assert serve_utils.validate_log_file('replica_1.log', 1)

    def test_mismatched_replica_id(self, mock_exists):
        # ReplicaID does not match, return False.
        mock_exists.return_value = True
        assert not serve_utils.validate_log_file('replica_1.log', 2)

    def test_no_replica_id_in_filename(self, mock_exists):
        # File name is illegal, return False.
        mock_exists.return_value = True
        assert not serve_utils.validate_log_file('service.log', 1)

    def test_invalid_replica_id_format(self, mock_exists):
        # FileReplicaID raises ValueError, return False.
        mock_exists.return_value = True
        assert not serve_utils.validate_log_file('replica_abc.log', 1)

    def test_empty_filename(self, mock_exists):
        # Remote filename is empty, return False.
        mock_exists.return_value = True
        assert not serve_utils.validate_log_file('', 1)

    def test_valid_replica_id_with_underscores(self, mock_exists):
        # file_replica_id == target_replica_id.
        mock_exists.return_value = True
        assert serve_utils.validate_log_file('test_replica_1_launch.log', 1)

    def test_complex_filename_with_valid_id(self, mock_exists):
        # file_replica_id == target_replica_id.
        mock_exists.return_value = True
        assert serve_utils.validate_log_file('service-name_replica_123.log',
                                             123)
