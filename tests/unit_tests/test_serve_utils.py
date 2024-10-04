import pytest

from sky.serve import serve_utils


class TestExtractReplicaIdFromLaunchLogFileName:

    def test_extract_when_launch_log_file(self):
        assert 1 == serve_utils._extract_replica_id_from_launch_log_file_name(
            'replica_1_launch.log')

    def test_extract_when_log_file(self):
        assert 1 == serve_utils._extract_replica_id_from_launch_log_file_name(
            'replica_1.log')

    def test_when_path_expanded(self):
        assert 1 == serve_utils._extract_replica_id_from_launch_log_file_name(
            '/Users/firstlast/.sky/serve/vicuna/replica_1_launch.log')

    def test_extract_when_no_match_because_invalid_prefix(self):
        with pytest.raises(ValueError):
            serve_utils._extract_replica_id_from_launch_log_file_name(
                '/Users/firstlast/.sky/serve/vicuna/bad_prefix_1_launch.log')

    def test_extract_when_no_match_because_invalid_suffix(self):
        with pytest.raises(ValueError):
            serve_utils._extract_replica_id_from_launch_log_file_name(
                '/Users/firstlast/.sky/serve/vicuna/replica_1_invalid.log')
