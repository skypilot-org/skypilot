"""Tests for AWS adaptor."""

import threading
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from sky.adaptors import aws


def test_session_caching_behavior():
    """Test session caching: different profiles not cached, same profile cached."""
    with patch('sky.adaptors.aws._create_aws_object') as mock_create:

        # Create mock sessions
        mock_session1 = MagicMock()
        mock_session1.get_credentials.return_value = MagicMock()
        mock_session2 = MagicMock()
        mock_session2.get_credentials.return_value = MagicMock()
        mock_session3 = MagicMock()
        mock_session3.get_credentials.return_value = MagicMock()

        mock_create.side_effect = [mock_session1, mock_session2, mock_session3]

        aws.session.cache_clear()

        # Different profiles should NOT be cached together
        result1 = aws.session(check_credentials=True, profile='profile1')
        result2 = aws.session(check_credentials=True, profile='profile2')
        assert result1 is not result2
        assert mock_create.call_count == 2

        # Same profile should be cached
        result3 = aws.session(check_credentials=True, profile='profile1')
        assert result1 is result3
        assert mock_create.call_count == 2  # Still 2, not 3

        # None vs named profile
        result4 = aws.session(check_credentials=True, profile=None)
        assert result4 is not result1
        assert mock_create.call_count == 3


def test_session_error_handling():
    """Test session creation error handling."""
    # Test invalid profile raises error when credentials are missing
    with patch('sky.adaptors.aws._create_aws_object') as mock_create:
        mock_session = MagicMock()
        mock_session.get_credentials.return_value = None
        mock_create.return_value = mock_session
        aws.session.cache_clear()

        with pytest.raises(Exception) as exc_info:
            aws.session(check_credentials=True, profile='invalid')
        assert 'NoCredentialsError' in str(type(exc_info.value))

    # Test no credentials check allows missing credentials
    with patch('sky.adaptors.aws._create_aws_object') as mock_create:

        mock_session = MagicMock()
        mock_session.get_credentials.return_value = None
        mock_create.return_value = mock_session

        aws.session.cache_clear()

        # Should NOT raise error when check_credentials=False
        result = aws.session(check_credentials=False, profile='dev')
        assert result == mock_session


def test_resource_and_client_use_workspace_profile():
    """Test that resource() and client() retrieve workspace profile."""
    with patch('sky.adaptors.aws.get_workspace_profile') as mock_get_profile, \
         patch('sky.adaptors.aws.session') as mock_session, \
         patch('sky.adaptors.aws._create_aws_object') as mock_create:

        mock_get_profile.return_value = 'test-profile'
        mock_session_obj = MagicMock()
        mock_session.return_value = mock_session_obj
        mock_obj = MagicMock()
        mock_session_obj.resource.return_value = mock_obj
        mock_session_obj.client.return_value = mock_obj
        mock_create.return_value = mock_obj

        # Test resource
        aws.resource('ec2')
        assert mock_get_profile.call_count == 1

        # Test client
        aws.client('s3')
        assert mock_get_profile.call_count == 2


def test_sessions_share_one_loader(monkeypatch):
    """Sessions rebuilt per request and per thread share one botocore loader."""
    monkeypatch.setattr(aws, '_loaders', {}, raising=False)
    monkeypatch.delenv('AWS_DATA_PATH', raising=False)

    sessions = []
    for _ in range(3):
        aws.session.cache_clear()
        sessions.append(aws.session(check_credentials=False))
    thread_sessions = []
    t = threading.Thread(target=lambda: thread_sessions.append(
        aws.session(check_credentials=False)))
    t.start()
    t.join()
    sessions += thread_sessions

    loaders = [s._session.get_component('data_loader') for s in sessions]
    assert len({id(s) for s in sessions}) == 4
    assert all(loader is loaders[0] for loader in loaders)
    # boto3 appends its data path on every new session.
    paths = loaders[0].search_paths
    assert len(paths) == len(set(paths))

    c1 = sessions[0].client('ec2', region_name='us-east-1')
    c2 = sessions[1].client('ec2', region_name='us-west-2')
    assert (c1.meta.service_model._service_description is
            c2.meta.service_model._service_description)


def test_loader_per_data_path(monkeypatch, tmp_path):
    """A different AWS_DATA_PATH gets its own loader with that search path."""
    monkeypatch.setattr(aws, '_loaders', {}, raising=False)
    monkeypatch.delenv('AWS_DATA_PATH', raising=False)
    aws.session.cache_clear()
    default_loader = aws.session(
        check_credentials=False)._session.get_component('data_loader')

    monkeypatch.setenv('AWS_DATA_PATH', str(tmp_path))
    aws.session.cache_clear()
    custom_loader = aws.session(
        check_credentials=False)._session.get_component('data_loader')
    aws.session.cache_clear()

    assert custom_loader is not default_loader
    assert custom_loader.search_paths[0] == str(tmp_path)
