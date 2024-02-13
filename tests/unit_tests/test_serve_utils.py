import pytest

from sky.serve import serve_utils


class TestExtractReplicaIdFromLaunchLogFileName:

    def test_extract(self):
        assert 1 == serve_utils.extract_replica_id_from_launch_log_file_name(
            'replica_1_launch.log')

    def test_when_path_expanded(self):
        assert 1 == serve_utils.extract_replica_id_from_launch_log_file_name(
            '/Users/firstlast/.sky/serve/vicuna/replica_1_launch.log')

    def test_extract_when_no_match(self):
        with pytest.raises(ValueError):
            serve_utils.extract_replica_id_from_launch_log_file_name(
                '/Users/firstlast/.sky/serve/vicuna/bad_prefix_1_launch.log')
