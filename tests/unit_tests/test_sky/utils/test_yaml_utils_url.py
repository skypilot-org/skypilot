"""Unit tests for yaml_utils URL loading functions."""
import unittest
from unittest import mock

import pytest

from sky.utils import yaml_utils


class TestYamlUtilsURL:
    """Test URL loading functionality in yaml_utils."""

    def test_is_url_http(self):
        """Test is_url with http URLs."""
        assert yaml_utils.is_url('http://example.com/file.yaml')
        assert yaml_utils.is_url('http://example.com/file.yml')

    def test_is_url_https(self):
        """Test is_url with https URLs."""
        assert yaml_utils.is_url('https://example.com/file.yaml')
        assert yaml_utils.is_url('https://github.com/user/repo/file.yaml')

    def test_is_url_s3(self):
        """Test is_url with S3 URLs."""
        assert yaml_utils.is_url('s3://bucket/path/file.yaml')
        assert yaml_utils.is_url('s3://my-bucket/configs/task.yaml')

    def test_is_url_gcs(self):
        """Test is_url with GCS URLs."""
        assert yaml_utils.is_url('gs://bucket/path/file.yaml')
        assert yaml_utils.is_url('gs://my-bucket/configs/task.yaml')

    def test_is_url_azure(self):
        """Test is_url with Azure URLs."""
        assert yaml_utils.is_url('az://container/path/file.yaml')
        assert yaml_utils.is_url('az://my-container/configs/task.yaml')

    def test_is_url_local_path(self):
        """Test is_url with local file paths."""
        assert not yaml_utils.is_url('/path/to/file.yaml')
        assert not yaml_utils.is_url('./file.yaml')
        assert not yaml_utils.is_url('file.yaml')
        assert not yaml_utils.is_url('~/file.yaml')

    def test_is_url_file_scheme(self):
        """Test is_url with file:// scheme (treated as local)."""
        assert not yaml_utils.is_url('file:///path/to/file.yaml')

    @mock.patch('fsspec.open')
    def test_read_file_or_url_http(self, mock_open):
        """Test read_file_or_url with HTTP URL."""
        yaml_content = 'name: test-task\n'
        mock_file = mock.Mock()
        mock_file.read.return_value = yaml_content
        mock_file.__enter__ = mock.Mock(return_value=mock_file)
        mock_file.__exit__ = mock.Mock(return_value=False)
        mock_open.return_value = mock_file

        result = yaml_utils.read_file_or_url('https://example.com/file.yaml')

        assert result == yaml_content
        mock_open.assert_called_once_with('https://example.com/file.yaml', 'r')

    @mock.patch('fsspec.open')
    def test_read_file_or_url_s3(self, mock_open):
        """Test read_file_or_url with S3 URL."""
        yaml_content = 'name: s3-task\n'
        mock_file = mock.Mock()
        mock_file.read.return_value = yaml_content
        mock_file.__enter__ = mock.Mock(return_value=mock_file)
        mock_file.__exit__ = mock.Mock(return_value=False)
        mock_open.return_value = mock_file

        result = yaml_utils.read_file_or_url('s3://bucket/file.yaml')

        assert result == yaml_content
        mock_open.assert_called_once_with('s3://bucket/file.yaml', 'r')

    @mock.patch('fsspec.open')
    def test_read_yaml_all_from_url(self, mock_open):
        """Test read_yaml_all with URL."""
        yaml_content = """name: test-task
resources:
  cloud: aws
  instance_type: p3.2xlarge
"""
        mock_file = mock.Mock()
        mock_file.read.return_value = yaml_content
        mock_file.__enter__ = mock.Mock(return_value=mock_file)
        mock_file.__exit__ = mock.Mock(return_value=False)
        mock_open.return_value = mock_file

        result = yaml_utils.read_yaml_all('https://example.com/file.yaml')

        assert len(result) == 1
        assert result[0]['name'] == 'test-task'
        assert result[0]['resources']['cloud'] == 'aws'

    @mock.patch('fsspec.open')
    def test_read_yaml_all_from_url_multi_doc(self, mock_open):
        """Test read_yaml_all with URL containing multiple YAML documents."""
        yaml_content = """---
name: task1
---
name: task2
---
name: task3
"""
        mock_file = mock.Mock()
        mock_file.read.return_value = yaml_content
        mock_file.__enter__ = mock.Mock(return_value=mock_file)
        mock_file.__exit__ = mock.Mock(return_value=False)
        mock_open.return_value = mock_file

        result = yaml_utils.read_yaml_all('https://example.com/file.yaml')

        assert len(result) == 3
        assert result[0]['name'] == 'task1'
        assert result[1]['name'] == 'task2'
        assert result[2]['name'] == 'task3'

    @mock.patch('fsspec.open')
    def test_read_yaml_from_url(self, mock_open):
        """Test read_yaml with URL."""
        yaml_content = """name: test-task
resources:
  cloud: gcp
  accelerators: V100:1
"""
        mock_file = mock.Mock()
        mock_file.read.return_value = yaml_content
        mock_file.__enter__ = mock.Mock(return_value=mock_file)
        mock_file.__exit__ = mock.Mock(return_value=False)
        mock_open.return_value = mock_file

        result = yaml_utils.read_yaml('https://example.com/file.yaml')

        assert result['name'] == 'test-task'
        assert result['resources']['cloud'] == 'gcp'
        assert result['resources']['accelerators'] == 'V100:1'

    @mock.patch('fsspec.open')
    def test_read_yaml_all_from_github_raw_url(self, mock_open):
        """Test read_yaml_all with realistic GitHub raw URL."""
        yaml_content = """name: qwen3-235b
resources:
  accelerators: {A100:8, H100:8}
  memory: 32+
  use_spot: true

num_nodes: 2

setup: |
  pip install torch transformers

run: |
  python train.py
"""
        mock_file = mock.Mock()
        mock_file.read.return_value = yaml_content
        mock_file.__enter__ = mock.Mock(return_value=mock_file)
        mock_file.__exit__ = mock.Mock(return_value=False)
        mock_open.return_value = mock_file

        url = ('https://raw.githubusercontent.com/skypilot-org/skypilot/'
               'refs/heads/master/llm/qwen/qwen3-235b.yaml')
        result = yaml_utils.read_yaml_all(url)

        assert len(result) == 1
        assert result[0]['name'] == 'qwen3-235b'
        assert result[0]['num_nodes'] == 2
        assert result[0]['resources']['use_spot'] is True


if __name__ == '__main__':
    unittest.main()
