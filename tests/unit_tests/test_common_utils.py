from unittest import mock

import pytest

from sky import exceptions
from sky.utils import common_utils

MOCKED_USER_HASH = 'ab12cd34'


class TestCheckClusterNameIsValid:

    def test_check(self):
        common_utils.check_cluster_name_is_valid("lora")

    def test_check_with_hyphen(self):
        common_utils.check_cluster_name_is_valid("seed-1")

    def test_check_with_characters_to_transform(self):
        common_utils.check_cluster_name_is_valid("Cuda_11.8")

    def test_check_when_starts_with_number(self):
        with pytest.raises(exceptions.InvalidClusterNameError):
            common_utils.check_cluster_name_is_valid("11.8cuda")

    def test_check_with_invalid_characters(self):
        with pytest.raises(exceptions.InvalidClusterNameError):
            common_utils.check_cluster_name_is_valid("lor@")

    def test_check_when_none(self):
        common_utils.check_cluster_name_is_valid(None)


class TestMakeClusterNameOnCloud:

    @mock.patch('sky.utils.common_utils.get_user_hash')
    def test_make(self, mock_get_user_hash):
        mock_get_user_hash.return_value = MOCKED_USER_HASH
        assert "lora-ab12" == common_utils.make_cluster_name_on_cloud("lora")

    @mock.patch('sky.utils.common_utils.get_user_hash')
    def test_make_with_hyphen(self, mock_get_user_hash):
        mock_get_user_hash.return_value = MOCKED_USER_HASH
        assert "seed-1-ab12" == common_utils.make_cluster_name_on_cloud(
            "seed-1")

    @mock.patch('sky.utils.common_utils.get_user_hash')
    def test_make_with_characters_to_transform(self, mock_get_user_hash):
        mock_get_user_hash.return_value = MOCKED_USER_HASH
        assert "cuda-11-8-ab12" == common_utils.make_cluster_name_on_cloud(
            "Cuda_11.8")
