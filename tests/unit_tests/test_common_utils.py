import os
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
        assert "lora-ab12cd34" == common_utils.make_cluster_name_on_cloud(
            "lora")

    @mock.patch('sky.utils.common_utils.get_user_hash')
    def test_make_with_hyphen(self, mock_get_user_hash):
        mock_get_user_hash.return_value = MOCKED_USER_HASH
        assert "seed-1-ab12cd34" == common_utils.make_cluster_name_on_cloud(
            "seed-1")

    @mock.patch('sky.utils.common_utils.get_user_hash')
    def test_make_with_characters_to_transform(self, mock_get_user_hash):
        mock_get_user_hash.return_value = MOCKED_USER_HASH
        assert "cud-73-ab12cd34" == common_utils.make_cluster_name_on_cloud(
            "Cuda_11.8")
        assert "cuda-11-8-ab12cd34" == common_utils.make_cluster_name_on_cloud(
            "Cuda_11.8", max_length=20)


class TestCgroupFunctions:
    """Test cgroup-related functions."""

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('sky.utils.common_utils._is_cgroup_v2')
    def test_get_cgroup_cpu_limit_v2(self, mock_is_v2, mock_open):
        # Test cgroup v2 CPU limit
        mock_is_v2.return_value = True
        mock_open.return_value.__enter__().read.return_value = '100000 100000'
        assert common_utils._get_cgroup_cpu_limit() == 1.0

        # Test no limit ("max")
        mock_open.return_value.__enter__().read.return_value = 'max 100000'
        assert common_utils._get_cgroup_cpu_limit() is None

        # Test partial cores
        mock_open.return_value.__enter__().read.return_value = '50000 100000'
        assert common_utils._get_cgroup_cpu_limit() == 0.5

        # Test file read error
        mock_open.side_effect = IOError
        assert common_utils._get_cgroup_cpu_limit() is None

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('sky.utils.common_utils._is_cgroup_v2')
    def test_get_cgroup_cpu_limit_v1(self, mock_is_v2, mock_open):
        # Test cgroup v1 CPU limit
        mock_is_v2.return_value = False

        # Mock both quota and period files
        mock_files = {
            '/sys/fs/cgroup/cpu/cpu.cfs_quota_us':
                mock.mock_open(read_data='100000').return_value,
            '/sys/fs/cgroup/cpu/cpu.cfs_period_us':
                mock.mock_open(read_data='100000').return_value,
        }
        mock_open.side_effect = lambda path, *args, **kwargs: mock_files[path]

        assert common_utils._get_cgroup_cpu_limit() == 1.0

        # Test partial cores
        mock_files = {
            '/sys/fs/cgroup/cpu/cpu.cfs_quota_us':
                mock.mock_open(read_data='50000').return_value,
            '/sys/fs/cgroup/cpu/cpu.cfs_period_us':
                mock.mock_open(read_data='100000').return_value,
        }
        mock_open.side_effect = lambda path, *args, **kwargs: mock_files[path]

        assert common_utils._get_cgroup_cpu_limit() == 0.5

        # Test no limit (-1)
        mock_files = {
            '/sys/fs/cgroup/cpu/cpu.cfs_quota_us': mock.mock_open(read_data='-1'
                                                                 ).return_value,
            '/sys/fs/cgroup/cpu/cpu.cfs_period_us':
                mock.mock_open(read_data='100000').return_value,
        }
        mock_open.side_effect = lambda path, *args, **kwargs: mock_files[path]

        assert common_utils._get_cgroup_cpu_limit() is None

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('sky.utils.common_utils._is_cgroup_v2')
    def test_get_cgroup_memory_limit_v2(self, mock_is_v2, mock_open):
        # Test cgroup v2 memory limit
        mock_is_v2.return_value = True

        # Test normal limit (8GB)
        mock_open.return_value.__enter__().read.return_value = str(8 * 1024**3)
        assert common_utils._get_cgroup_memory_limit() == 8 * 1024**3

        # Test no limit ("max")
        mock_open.return_value.__enter__().read.return_value = 'max'
        assert common_utils._get_cgroup_memory_limit() is None

        # Test empty value
        mock_open.return_value.__enter__().read.return_value = ''
        assert common_utils._get_cgroup_memory_limit() is None

        # Test file read error
        mock_open.side_effect = IOError
        assert common_utils._get_cgroup_memory_limit() is None

    @mock.patch('builtins.open', new_callable=mock.mock_open)
    @mock.patch('sky.utils.common_utils._is_cgroup_v2')
    def test_get_cgroup_memory_limit_v1(self, mock_is_v2, mock_open):
        # Test cgroup v1 memory limit
        mock_is_v2.return_value = False

        # Test normal limit (1GB)
        mock_open.return_value.__enter__().read.return_value = str(1024**3)
        assert common_utils._get_cgroup_memory_limit() == 1024**3

        # Test empty value
        mock_open.return_value.__enter__().read.return_value = ''
        assert common_utils._get_cgroup_memory_limit() is None

        # Test file read error
        mock_open.side_effect = IOError
        assert common_utils._get_cgroup_memory_limit() is None

    @mock.patch('os.path.isfile')
    def test_is_cgroup_v2(self, mock_exists):
        # Test cgroup v2 detection
        mock_exists.return_value = True
        assert common_utils._is_cgroup_v2() is True

        mock_exists.return_value = False
        assert common_utils._is_cgroup_v2() is False

    @mock.patch('psutil.cpu_count')
    @mock.patch('sky.utils.common_utils._get_cgroup_cpu_limit')
    @mock.patch('os.sched_getaffinity', create=True)
    def test_get_cpu_count(self, mock_affinity, mock_cgroup_cpu,
                           mock_cpu_count):
        # Test when no cgroup limit
        mock_cpu_count.return_value = 8
        mock_cgroup_cpu.return_value = None
        # Non-Linux platforms
        delattr(os, 'sched_getaffinity')
        assert common_utils.get_cpu_count() == 8

        # Test with CPU affinity
        setattr(os, 'sched_getaffinity', mock_affinity)
        mock_affinity.return_value = {0, 1, 2, 3}
        assert common_utils.get_cpu_count() == 4

        # Test when cgroup limit is higher
        mock_cgroup_cpu.return_value = 16.0
        assert common_utils.get_cpu_count() == 4

        # Test when cgroup limit is lower
        mock_cgroup_cpu.return_value = 2.0
        assert common_utils.get_cpu_count() == 2

        # Test with env var
        with mock.patch.dict('os.environ',
                             {'SKYPILOT_POD_CPU_CORE_LIMIT': '2'}):
            assert common_utils.get_cpu_count() == 2

    @mock.patch('psutil.virtual_memory')
    @mock.patch('sky.utils.common_utils._get_cgroup_memory_limit')
    def test_get_mem_size_gb(self, mock_cgroup_mem, mock_virtual_memory):
        # Test when no cgroup limit
        mock_virtual_memory.return_value.total = 8 * 1024**3  # 8GB
        mock_cgroup_mem.return_value = None
        assert common_utils.get_mem_size_gb() == 8.0

        # Test when cgroup limit is lower
        mock_cgroup_mem.return_value = 4 * 1024**3  # 4GB
        assert common_utils.get_mem_size_gb() == 4.0

        # Test when cgroup limit is higher
        mock_cgroup_mem.return_value = 16 * 1024**3  # 16GB
        assert common_utils.get_mem_size_gb() == 8.0

        # Test with env var
        with mock.patch.dict('os.environ',
                             {'SKYPILOT_POD_MEMORY_GB_LIMIT': '2'}):
            assert common_utils.get_mem_size_gb() == 2.0
