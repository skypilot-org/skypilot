from unittest.mock import patch

from sky.utils import common_utils

MOCKED_USER_HASH = 'ab12cd34'


class TestMakeClusterNameOnCloud:

    @patch('sky.utils.common_utils.get_user_hash')
    def test_make(self, mock_get_user_hash):
        mock_get_user_hash.return_value = MOCKED_USER_HASH
        assert "lora-ab12" == common_utils.make_cluster_name_on_cloud("lora")

    @patch('sky.utils.common_utils.get_user_hash')
    def test_make_with_hyphen(self, mock_get_user_hash):
        mock_get_user_hash.return_value = MOCKED_USER_HASH
        assert "seed-1-ab12" == common_utils.make_cluster_name_on_cloud(
            "seed-1")

    @patch('sky.utils.common_utils.get_user_hash')
    def test_make_with_characters_to_transform(self, mock_get_user_hash):
        mock_get_user_hash.return_value = MOCKED_USER_HASH
        assert "cuda-11-8-ab12" == common_utils.make_cluster_name_on_cloud(
            "Cuda_11.8")
