"""Unit tests for AWS workspace profile functionality."""

from unittest import mock

from sky.adaptors import aws


def _create_aws_file(tmp_path, filename, contents):
    aws_dir = tmp_path / '.aws'
    aws_dir.mkdir(exist_ok=True)
    file_path = aws_dir / filename

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(contents)


def test_validate_workspace_profile(tmp_path, monkeypatch):
    """Test profile validation in various scenarios."""
    # Point ~ to tmp_path so we can mock the credentials file
    monkeypatch.setattr('os.path.expanduser',
                        lambda p: p.replace('~', str(tmp_path)))

    # Test profiles in credentials file are validated
    _create_aws_file(
        tmp_path, 'credentials', """[default]
aws_access_key_id = default_key
aws_secret_access_key = default_secret

[dev]
aws_access_key_id = dev_key
aws_secret_access_key = dev_secret
""")

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
    _create_aws_file(other_path, 'config', """[profile staging]
region = us-west-2
""")
    # Validation should fail because staging is only in config, not credentials
    assert not aws._validate_workspace_profile('staging')

    # Test validation fails when no files exist at all
    monkeypatch.setattr('os.path.expanduser',
                        lambda p: p.replace('~', str(tmp_path / 'nonexistent')))
    assert not aws._validate_workspace_profile('dev')


@mock.patch('sky.skypilot_config.get_workspace_cloud')
def test_get_workspace_profile(mock_get_workspace_cloud):
    """Test getting workspace profile when configured and not configured."""
    # Test when configured
    mock_get_workspace_cloud.return_value.get.return_value = 'dev-profile'
    assert aws.get_workspace_profile() == 'dev-profile'

    # Test when not configured
    mock_get_workspace_cloud.return_value.get.return_value = None
    assert aws.get_workspace_profile() is None
