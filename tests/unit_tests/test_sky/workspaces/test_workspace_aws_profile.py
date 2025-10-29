"""Unit tests for AWS workspace profile functionality."""

import configparser
import os
from typing import List
from unittest import mock

import pytest

from sky.adaptors import aws


def _create_credentials_file(tmp_path, profiles):
    """Helper to create a credentials file with given profiles.

    Args:
        tmp_path: Temporary directory path from pytest fixture
        profiles: Dict of profile_name -> {aws_access_key_id, aws_secret_access_key}
    """
    aws_dir = tmp_path / '.aws'
    aws_dir.mkdir(exist_ok=True)
    credentials_path = aws_dir / 'credentials'

    parser = configparser.ConfigParser()
    for profile_name, creds in profiles.items():
        parser[profile_name] = creds
    with open(credentials_path, 'w', encoding='utf-8') as f:
        parser.write(f)


def _create_config_file(tmp_path, profiles):
    """Helper to create a config file with given profiles.

    Args:
        tmp_path: Temporary directory path from pytest fixture
        profiles: Dict of profile_name -> {region, output, etc.}
    """
    aws_dir = tmp_path / '.aws'
    aws_dir.mkdir(exist_ok=True)
    config_path = aws_dir / 'config'

    parser = configparser.ConfigParser()
    for profile_name, config in profiles.items():
        if profile_name == 'default':
            parser['default'] = config
        else:
            parser[f'profile {profile_name}'] = config
    with open(config_path, 'w', encoding='utf-8') as f:
        parser.write(f)


def _get_credential_file_paths() -> List[str]:
    """Helper to get credential file paths for testing.

    Returns:
        List of paths to AWS credential files that should be mounted.
    """
    paths = []
    for filename in ['credentials', 'config']:
        path = os.path.expanduser(f'~/.aws/{filename}')
        if os.path.exists(path):
            paths.append(f'~/.aws/{filename}')
    return paths


def test_validate_workspace_profile(tmp_path, monkeypatch):
    """Test profile validation in various scenarios."""
    # Point ~ to tmp_path so we can mock the credentials file
    monkeypatch.setattr('os.path.expanduser',
                        lambda p: p.replace('~', str(tmp_path)))

    # Test profiles in credentials file are validated
    _create_credentials_file(
        tmp_path, {
            'default': {
                'aws_access_key_id': 'default_key',
                'aws_secret_access_key': 'default_secret'
            },
            'dev': {
                'aws_access_key_id': 'dev_key',
                'aws_secret_access_key': 'dev_secret'
            }
        })

    # Profiles in credentials file should be found
    assert aws._validate_workspace_profile('dev')
    assert aws._validate_workspace_profile('default')
    # Non-existent profile should not be found
    assert not aws._validate_workspace_profile('prod')

    # Test that profiles ONLY in config file are NOT validated
    # (Workspace profiles require actual credentials, not SSO/assume-role)

    # Create a separate directory to ensure no credentials file exists
    # (we need a clean slate since tmp_path already has credentials)
    other_path = tmp_path / 'other'
    other_path.mkdir()
    # Redirect ~ to this new directory
    monkeypatch.setattr('os.path.expanduser',
                        lambda p: p.replace('~', str(other_path)))

    # Create ONLY a config file with 'staging' profile (no credentials file)
    _create_config_file(other_path, {'staging': {'region': 'us-west-2'}})
    # Validation should fail because staging is only in config, not credentials
    assert not aws._validate_workspace_profile('staging')

    # Test validation fails when no files exist at all
    monkeypatch.setattr('os.path.expanduser',
                        lambda p: p.replace('~', str(tmp_path / 'nonexistent')))
    assert not aws._validate_workspace_profile('dev')


@mock.patch('sky.skypilot_config.get_workspace_cloud')
def test_get_current_profile(mock_get_workspace_cloud, tmp_path, monkeypatch):
    """Test getting current profile from workspace config and disk fallback."""
    # Point ~ to tmp_path for testing disk fallback
    monkeypatch.setattr('os.path.expanduser',
                        lambda p: p.replace('~', str(tmp_path)))

    # Profile from workspace config (primary source)
    mock_get_workspace_cloud.return_value.get.return_value = 'dev-profile'
    assert aws.get_current_profile() == 'dev-profile'

    # No profile in workspace config, fallback to disk
    mock_get_workspace_cloud.return_value.get.return_value = None

    # Create ~/.sky/.aws_profile with a profile name
    sky_dir = tmp_path / '.sky'
    sky_dir.mkdir(exist_ok=True)
    profile_file = sky_dir / '.aws_profile'
    profile_file.write_text('remote-profile')

    assert aws.get_current_profile() == 'remote-profile'

    # No profile in workspace config and no disk file
    profile_file.unlink()
    assert aws.get_current_profile() is None

    # Empty profile file should return None
    profile_file.write_text('')
    assert aws.get_current_profile() is None

    # Profile file with whitespace should be stripped
    profile_file.write_text('  trimmed-profile  \n')
    assert aws.get_current_profile() == 'trimmed-profile'
