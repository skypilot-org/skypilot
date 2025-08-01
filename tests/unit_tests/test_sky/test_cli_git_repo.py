"""Unit tests for git repository related functions in command.py."""

import os
from unittest.mock import MagicMock
from unittest.mock import patch
import unittest.mock as mock

import pytest

import sky
from sky import exceptions
from sky.client.cli import command
from sky.client.cli import git
from sky.utils import git as git_utils
from sky.utils import ux_utils


class TestUpdateTaskWorkdir:
    """Test cases for _update_task_workdir function."""

    def test_update_task_workdir_none_workdir_with_git_url(self):
        """Test updating task workdir when workdir is None and git_url is provided."""
        task = sky.Task(name='test', run='echo hello')
        task.workdir = None

        command._update_task_workdir(task, None,
                                     'https://github.com/test/repo.git', 'main')

        assert task.workdir == {
            'url': 'https://github.com/test/repo.git',
            'ref': 'main'
        }

    def test_update_task_workdir_none_workdir_no_git_url(self):
        """Test updating task workdir when workdir is None and no git_url is provided."""
        task = sky.Task(name='test', run='echo hello')
        task.workdir = None

        command._update_task_workdir(task, None, None, 'main')

        assert task.workdir is None

    def test_update_task_workdir_none_workdir_with_git_url_none_ref(self):
        """Test updating task workdir when workdir is None, git_url is provided, and ref is None."""
        task = sky.Task(name='test', run='echo hello')
        task.workdir = None

        command._update_task_workdir(task, None,
                                     'https://github.com/test/repo.git', None)

        assert task.workdir == {
            'url': 'https://github.com/test/repo.git',
        }

    def test_update_task_workdir_dict_workdir_update_url(self):
        """Test updating task workdir when workdir is a dict and git_url is provided."""
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'https://github.com/old/repo.git',
            'ref': 'old_ref'
        }

        command._update_task_workdir(task, None,
                                     'https://github.com/new/repo.git', None)

        assert task.workdir == {
            'url': 'https://github.com/new/repo.git',
            'ref': 'old_ref'
        }

    def test_update_task_workdir_dict_workdir_update_ref(self):
        """Test updating task workdir when workdir is a dict and git_ref is provided."""
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'https://github.com/test/repo.git',
            'ref': 'old_ref'
        }

        command._update_task_workdir(task, None, None, 'new_ref')

        assert task.workdir == {
            'url': 'https://github.com/test/repo.git',
            'ref': 'new_ref'
        }

    def test_update_task_workdir_dict_workdir_update_both(self):
        """Test updating task workdir when workdir is a dict and both git_url and git_ref are provided."""
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'https://github.com/old/repo.git',
            'ref': 'old_ref'
        }

        command._update_task_workdir(task, None,
                                     'https://github.com/new/repo.git',
                                     'new_ref')

        assert task.workdir == {
            'url': 'https://github.com/new/repo.git',
            'ref': 'new_ref'
        }

    def test_update_task_workdir_string_workdir(self):
        """Test updating task workdir when workdir is a string."""
        task = sky.Task(name='test', run='echo hello')
        task.workdir = '/some/local/path'

        command._update_task_workdir(task, None,
                                     'https://github.com/test/repo.git', 'main')

        assert task.workdir == {
            'url': 'https://github.com/test/repo.git',
            'ref': 'main'
        }

    def test_update_task_workdir_string_workdir_update(self):
        """Test updating task workdir when workdir is a string and override workdir is provided."""
        task = sky.Task(name='test', run='echo hello')
        task.workdir = '/some/local/path'

        command._update_task_workdir(task, "abcd",
                                     'https://github.com/test/repo.git', 'main')

        assert task.workdir == "abcd"

    def test_update_task_workdir_no_changes(self):
        """Test updating task workdir when no git_url or git_ref is provided."""
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'https://github.com/test/repo.git',
            'ref': 'main'
        }

        command._update_task_workdir(task, None, None, None)

        assert task.workdir == {
            'url': 'https://github.com/test/repo.git',
            'ref': 'main'
        }


class TestUpdateTaskWorkdirAndSecretsFromWorkdir:
    """Test cases for _update_task_workdir_and_secrets_from_workdir function."""

    def test_update_task_workdir_and_secrets_none_workdir(self):
        """Test updating task secrets when workdir is None."""
        task = sky.Task(name='test', run='echo hello')
        task.workdir = None

        command._update_task_workdir_and_secrets_from_workdir(task)

        # Should not raise any exception and task should remain unchanged
        assert task.workdir is None

    def test_update_task_workdir_and_secrets_string_workdir(self):
        """Test updating task secrets when workdir is a string."""
        task = sky.Task(name='test', run='echo hello')
        task.workdir = '/some/local/path'

        command._update_task_workdir_and_secrets_from_workdir(task)

        # Should not raise any exception and task should remain unchanged
        assert task.workdir == '/some/local/path'

    @patch.dict(os.environ, {}, clear=True)
    @patch('sky.client.cli.git.GitRepo')
    def test_update_task_workdir_and_secrets_with_token(self, mock_git_repo):
        """Test updating task secrets when git token is available."""
        # Set up environment
        os.environ[git_utils.GIT_TOKEN_ENV_VAR] = 'test_token'

        # Set up task
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'https://github.com/test/repo.git',
            'ref': 'main'
        }

        # Mock GitRepo and clone info
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/test/repo.git'
        mock_clone_info.token = 'test_token'
        mock_clone_info.ssh_key = None

        mock_repo_instance = MagicMock()
        mock_repo_instance.get_repo_clone_info.return_value = mock_clone_info
        mock_repo_instance.get_ref_type.return_value = git.GitRefType.BRANCH
        mock_git_repo.return_value = mock_repo_instance

        command._update_task_workdir_and_secrets_from_workdir(task)

        # Verify GitRepo was called with correct parameters
        mock_git_repo.assert_called_once_with(
            'https://github.com/test/repo.git', 'main', 'test_token', None)

        # Verify task envs and secrets were updated
        assert task.envs[
            git_utils.GIT_URL_ENV_VAR] == 'https://github.com/test/repo.git'
        assert task.secrets[git_utils.GIT_TOKEN_ENV_VAR] == 'test_token'
        assert task.envs[git_utils.GIT_BRANCH_ENV_VAR] == 'main'
        assert git_utils.GIT_SSH_KEY_ENV_VAR not in task.secrets

    @patch.dict(os.environ, {}, clear=True)
    @patch('sky.client.cli.git.GitRepo')
    def test_update_task_workdir_and_secrets_with_ssh_key(self, mock_git_repo):
        """Test updating task secrets when SSH key is available."""
        # Set up environment
        os.environ[git_utils.GIT_SSH_KEY_PATH_ENV_VAR] = '/path/to/ssh/key'

        # Set up task
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'ssh://git@github.com/test/repo.git',
            'ref': 'main'
        }

        # Mock GitRepo and clone info
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'ssh://git@github.com/test/repo.git'
        mock_clone_info.token = None
        mock_clone_info.ssh_key = 'ssh_key_content'

        mock_repo_instance = MagicMock()
        mock_repo_instance.get_repo_clone_info.return_value = mock_clone_info
        mock_repo_instance.get_ref_type.return_value = git.GitRefType.BRANCH
        mock_git_repo.return_value = mock_repo_instance

        command._update_task_workdir_and_secrets_from_workdir(task)

        # Verify GitRepo was called with correct parameters
        mock_git_repo.assert_called_once_with(
            'ssh://git@github.com/test/repo.git', 'main', None,
            '/path/to/ssh/key')

        # Verify task envs and secrets were updated
        assert task.envs[
            git_utils.GIT_URL_ENV_VAR] == 'ssh://git@github.com/test/repo.git'
        assert task.secrets[git_utils.GIT_SSH_KEY_ENV_VAR] == 'ssh_key_content'
        assert task.envs[git_utils.GIT_BRANCH_ENV_VAR] == 'main'
        assert git_utils.GIT_TOKEN_ENV_VAR not in task.secrets

    @patch.dict(os.environ, {}, clear=True)
    @patch('sky.client.cli.git.GitRepo')
    def test_update_task_workdir_and_secrets_with_commit_hash(
            self, mock_git_repo):
        """Test updating task secrets when ref is a commit hash."""
        # Set up environment
        os.environ[git_utils.GIT_TOKEN_ENV_VAR] = 'test_token'

        # Set up task
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'https://github.com/test/repo.git',
            'ref': 'a1b2c3d4e5f6'
        }

        # Mock GitRepo and clone info
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/test/repo.git'
        mock_clone_info.token = 'test_token'
        mock_clone_info.ssh_key = None

        mock_repo_instance = MagicMock()
        mock_repo_instance.get_repo_clone_info.return_value = mock_clone_info
        mock_repo_instance.get_ref_type.return_value = git.GitRefType.COMMIT
        mock_git_repo.return_value = mock_repo_instance

        command._update_task_workdir_and_secrets_from_workdir(task)

        # Verify task envs were updated with commit hash
        assert task.envs[
            git_utils.GIT_URL_ENV_VAR] == 'https://github.com/test/repo.git'
        assert task.envs[git_utils.GIT_COMMIT_HASH_ENV_VAR] == 'a1b2c3d4e5f6'
        assert git_utils.GIT_BRANCH_ENV_VAR not in task.envs

    @patch.dict(os.environ, {}, clear=True)
    @patch('sky.client.cli.git.GitRepo')
    def test_update_task_workdir_and_secrets_with_tag(self, mock_git_repo):
        """Test updating task secrets when ref is a tag."""
        # Set up environment
        os.environ[git_utils.GIT_TOKEN_ENV_VAR] = 'test_token'

        # Set up task
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'https://github.com/test/repo.git',
            'ref': 'v1.0.0'
        }

        # Mock GitRepo and clone info
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/test/repo.git'
        mock_clone_info.token = 'test_token'
        mock_clone_info.ssh_key = None

        mock_repo_instance = MagicMock()
        mock_repo_instance.get_repo_clone_info.return_value = mock_clone_info
        mock_repo_instance.get_ref_type.return_value = git.GitRefType.TAG
        mock_git_repo.return_value = mock_repo_instance

        command._update_task_workdir_and_secrets_from_workdir(task)

        # Verify task envs were updated with tag
        assert task.envs[
            git_utils.GIT_URL_ENV_VAR] == 'https://github.com/test/repo.git'
        assert task.envs[git_utils.GIT_TAG_ENV_VAR] == 'v1.0.0'
        assert git_utils.GIT_BRANCH_ENV_VAR not in task.envs

    @patch.dict(os.environ, {}, clear=True)
    @patch('sky.client.cli.git.GitRepo')
    def test_update_task_workdir_and_secrets_empty_ref(self, mock_git_repo):
        """Test updating task secrets when ref is empty."""
        # Set up environment
        os.environ[git_utils.GIT_TOKEN_ENV_VAR] = 'test_token'

        # Set up task
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {'url': 'https://github.com/test/repo.git'}

        # Mock GitRepo and clone info
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/test/repo.git'
        mock_clone_info.token = 'test_token'
        mock_clone_info.ssh_key = None

        mock_repo_instance = MagicMock()
        mock_repo_instance.get_repo_clone_info.return_value = mock_clone_info
        mock_repo_instance.get_ref_type.return_value = git.GitRefType.BRANCH
        mock_git_repo.return_value = mock_repo_instance

        command._update_task_workdir_and_secrets_from_workdir(task)

        # Verify task envs were updated but no branch/commit hash env
        assert task.envs[
            git_utils.GIT_URL_ENV_VAR] == 'https://github.com/test/repo.git'
        assert git_utils.GIT_BRANCH_ENV_VAR not in task.envs
        assert git_utils.GIT_COMMIT_HASH_ENV_VAR not in task.envs

    @patch.dict(os.environ, {}, clear=True)
    @patch('sky.client.cli.git.GitRepo')
    def test_update_task_workdir_and_secrets_none_clone_info(
            self, mock_git_repo):
        """Test updating task secrets when clone info is None."""
        # Set up environment
        os.environ[git_utils.GIT_TOKEN_ENV_VAR] = 'test_token'

        # Set up task
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'https://github.com/test/repo.git',
            'ref': 'main'
        }

        # Mock GitRepo to return None clone info
        mock_repo_instance = MagicMock()
        mock_repo_instance.get_repo_clone_info.return_value = None
        mock_git_repo.return_value = mock_repo_instance

        command._update_task_workdir_and_secrets_from_workdir(task)

        # Verify task was not modified
        assert git_utils.GIT_URL_ENV_VAR not in task.envs
        assert git_utils.GIT_TOKEN_ENV_VAR not in task.secrets
        assert git_utils.GIT_SSH_KEY_ENV_VAR not in task.secrets

    @patch.dict(os.environ, {}, clear=True)
    @patch('sky.client.cli.git.GitRepo')
    def test_update_task_workdir_and_secrets_no_auth(self, mock_git_repo):
        """Test updating task secrets when no authentication is available."""
        # Set up task
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'https://github.com/test/repo.git',
            'ref': 'main'
        }

        # Mock GitRepo and clone info with no auth
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/test/repo.git'
        mock_clone_info.token = None
        mock_clone_info.ssh_key = None

        mock_repo_instance = MagicMock()
        mock_repo_instance.get_repo_clone_info.return_value = mock_clone_info
        mock_git_repo.return_value = mock_repo_instance

        # Mock get_ref_type to return branch
        mock_repo_instance.get_ref_type.return_value = git.GitRefType.BRANCH

        command._update_task_workdir_and_secrets_from_workdir(task)

        # Verify task was not modified (no auth available)
        assert git_utils.GIT_URL_ENV_VAR in task.envs
        assert git_utils.GIT_BRANCH_ENV_VAR in task.envs
        assert git_utils.GIT_TOKEN_ENV_VAR not in task.secrets
        assert git_utils.GIT_SSH_KEY_ENV_VAR not in task.secrets

    @patch.dict(os.environ, {}, clear=True)
    @patch('sky.client.cli.git.GitRepo')
    @patch('sky.utils.ux_utils.print_exception_no_traceback')
    def test_update_task_workdir_and_secrets_git_error(self,
                                                       mock_print_exception,
                                                       mock_git_repo):
        """Test updating task secrets when GitError is raised."""
        # Set up environment
        os.environ[git_utils.GIT_TOKEN_ENV_VAR] = 'test_token'

        # Set up task
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'https://github.com/test/repo.git',
            'ref': 'main'
        }

        # Mock GitRepo to raise GitError
        mock_git_repo.side_effect = exceptions.GitError('Test git error')

        with pytest.raises(ValueError, match='Test git error'):
            command._update_task_workdir_and_secrets_from_workdir(task)

        # Verify print_exception_no_traceback was called
        mock_print_exception.assert_called_once()

    @patch.dict(os.environ, {}, clear=True)
    @patch('sky.client.cli.git.GitRepo')
    def test_update_task_workdir_and_secrets_both_auth_types(
            self, mock_git_repo):
        """Test updating task secrets when both token and SSH key are available."""
        # Set up environment
        os.environ[git_utils.GIT_TOKEN_ENV_VAR] = 'test_token'
        os.environ[git_utils.GIT_SSH_KEY_PATH_ENV_VAR] = '/path/to/ssh/key'

        # Set up task
        task = sky.Task(name='test', run='echo hello')
        task.workdir = {
            'url': 'https://github.com/test/repo.git',
            'ref': 'main'
        }

        # Mock GitRepo and clone info with both auth types
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/test/repo.git'
        mock_clone_info.token = 'test_token'
        mock_clone_info.ssh_key = 'ssh_key_content'

        mock_repo_instance = MagicMock()
        mock_repo_instance.get_repo_clone_info.return_value = mock_clone_info
        mock_repo_instance.get_ref_type.return_value = git.GitRefType.BRANCH
        mock_git_repo.return_value = mock_repo_instance

        command._update_task_workdir_and_secrets_from_workdir(task)

        # Verify both auth types were set
        assert task.envs[
            git_utils.GIT_URL_ENV_VAR] == 'https://github.com/test/repo.git'
        assert task.secrets[git_utils.GIT_TOKEN_ENV_VAR] == 'test_token'
        assert task.secrets[git_utils.GIT_SSH_KEY_ENV_VAR] == 'ssh_key_content'
        assert task.envs[git_utils.GIT_BRANCH_ENV_VAR] == 'main'


class TestGitUrlInfo:
    """Test cases for GitUrlInfo class."""

    def test_git_url_info_init(self):
        """Test GitUrlInfo initialization."""
        url_info = git.GitUrlInfo(host='github.com',
                                  path='user/repo',
                                  protocol='https',
                                  user='test_user',
                                  port=443)

        assert url_info.host == 'github.com'
        assert url_info.path == 'user/repo'
        assert url_info.protocol == 'https'
        assert url_info.user == 'test_user'
        assert url_info.port == 443

    def test_git_url_info_init_optional_params(self):
        """Test GitUrlInfo initialization with optional parameters."""
        url_info = git.GitUrlInfo(host='gitlab.com',
                                  path='org/project',
                                  protocol='ssh')

        assert url_info.host == 'gitlab.com'
        assert url_info.path == 'org/project'
        assert url_info.protocol == 'ssh'
        assert url_info.user is None
        assert url_info.port is None


class TestGitCloneInfo:
    """Test cases for GitCloneInfo class."""

    def test_git_clone_info_init(self):
        """Test GitCloneInfo initialization."""
        clone_info = git.GitCloneInfo(url='https://github.com/test/repo.git',
                                      envs={'GIT_SSH_COMMAND': 'ssh -i key'},
                                      token='test_token',
                                      ssh_key='ssh_key_content')

        assert clone_info.url == 'https://github.com/test/repo.git'
        assert clone_info.envs == {'GIT_SSH_COMMAND': 'ssh -i key'}
        assert clone_info.token == 'test_token'
        assert clone_info.ssh_key == 'ssh_key_content'

    def test_git_clone_info_init_minimal(self):
        """Test GitCloneInfo initialization with minimal parameters."""
        clone_info = git.GitCloneInfo(url='https://github.com/test/repo.git')

        assert clone_info.url == 'https://github.com/test/repo.git'
        assert clone_info.envs is None
        assert clone_info.token is None
        assert clone_info.ssh_key is None


class TestGitRepo:
    """Test cases for GitRepo class."""

    def test_git_repo_init(self):
        """Test GitRepo initialization."""
        repo = git.GitRepo(repo_url='https://github.com/test/repo.git',
                           ref='main',
                           git_token='test_token',
                           git_ssh_key_path='/path/to/key')

        assert repo.repo_url == 'https://github.com/test/repo.git'
        assert repo.ref == 'main'
        assert repo.git_token == 'test_token'
        assert repo.git_ssh_key_path == '/path/to/key'
        assert repo._parsed_url is not None

    def test_git_repo_init_defaults(self):
        """Test GitRepo initialization with default values."""
        repo = git.GitRepo(repo_url='https://github.com/test/repo.git')

        assert repo.repo_url == 'https://github.com/test/repo.git'
        assert repo.ref == 'main'
        assert repo.git_token is None
        assert repo.git_ssh_key_path is None

    def test_parse_git_url_https(self):
        """Test parsing HTTPS git URL."""
        repo = git.GitRepo(repo_url='https://github.com/user/repo.git')

        assert repo._parsed_url.host == 'github.com'
        assert repo._parsed_url.path == 'user/repo'
        assert repo._parsed_url.protocol == 'https'
        assert repo._parsed_url.user is None
        assert repo._parsed_url.port is None

    def test_parse_git_url_https_with_port(self):
        """Test parsing HTTPS git URL with port."""
        repo = git.GitRepo(
            repo_url='https://gitlab.example.com:8080/user/repo.git')

        assert repo._parsed_url.host == 'gitlab.example.com'
        assert repo._parsed_url.path == 'user/repo'
        assert repo._parsed_url.protocol == 'https'
        assert repo._parsed_url.user is None
        assert repo._parsed_url.port == 8080

    def test_parse_git_url_https_with_user(self):
        """Test parsing HTTPS git URL with user."""
        repo = git.GitRepo(repo_url='https://user@github.com/user/repo.git')

        assert repo._parsed_url.host == 'github.com'
        assert repo._parsed_url.path == 'user/repo'
        assert repo._parsed_url.protocol == 'https'
        assert repo._parsed_url.user == 'user'
        assert repo._parsed_url.port is None

    def test_parse_git_url_ssh_full(self):
        """Test parsing SSH git URL in full format."""
        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')

        assert repo._parsed_url.host == 'github.com'
        assert repo._parsed_url.path == 'user/repo'
        assert repo._parsed_url.protocol == 'ssh'
        assert repo._parsed_url.user == 'git'
        assert repo._parsed_url.port is None

    def test_parse_git_url_ssh_full_with_port(self):
        """Test parsing SSH git URL in full format with port."""
        repo = git.GitRepo(repo_url='ssh://git@gitlab.com:2222/user/repo.git')

        assert repo._parsed_url.host == 'gitlab.com'
        assert repo._parsed_url.path == 'user/repo'
        assert repo._parsed_url.protocol == 'ssh'
        assert repo._parsed_url.user == 'git'
        assert repo._parsed_url.port == 2222

    def test_parse_git_url_ssh_scp_format(self):
        """Test parsing SSH git URL in SCP format."""
        repo = git.GitRepo(repo_url='git@github.com:user/repo.git')

        assert repo._parsed_url.host == 'github.com'
        assert repo._parsed_url.path == 'user/repo'
        assert repo._parsed_url.protocol == 'ssh'
        assert repo._parsed_url.user == 'git'
        assert repo._parsed_url.port is None

    def test_parse_git_url_without_git_suffix(self):
        """Test parsing git URL without .git suffix."""
        repo = git.GitRepo(repo_url='https://github.com/user/repo')

        assert repo._parsed_url.host == 'github.com'
        assert repo._parsed_url.path == 'user/repo'
        assert repo._parsed_url.protocol == 'https'

    def test_parse_git_url_invalid_empty_path(self):
        """Test parsing git URL with empty path raises error."""
        with pytest.raises(exceptions.GitError,
                           match='Invalid repository path'):
            git.GitRepo(repo_url='https://github.com//.git')

    def test_parse_git_url_invalid_ssh_full_empty_path(self):
        """Test parsing SSH full format URL with empty path raises error."""
        with pytest.raises(exceptions.GitError,
                           match='Invalid repository path in SSH URL'):
            git.GitRepo(repo_url='ssh://git@github.com//.git')

    def test_parse_git_url_invalid_scp_empty_path(self):
        """Test parsing SCP format URL with empty path raises error."""
        # Create a URL that matches SCP pattern but has empty path after processing
        with patch('sky.client.cli.git.re.match') as mock_match:
            # Mock the first two matches to return None (https and ssh_full)
            # Mock the third match (scp) to return empty path
            mock_match_obj = MagicMock()
            mock_match_obj.groups.return_value = ('git', 'github.com', '')
            mock_match.side_effect = [None, None, mock_match_obj]

            with pytest.raises(exceptions.GitError,
                               match='Invalid repository path in SSH URL'):
                git.GitRepo(repo_url='git@github.com:')

    def test_parse_git_url_invalid_format(self):
        """Test parsing invalid git URL format raises error."""
        with pytest.raises(exceptions.GitError,
                           match='Unsupported git URL format'):
            git.GitRepo(repo_url='invalid://url/format')

    def test_get_https_url_basic(self):
        """Test getting HTTPS URL without token."""
        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')
        https_url = repo.get_https_url()

        assert https_url == 'https://github.com/user/repo.git'

    def test_get_https_url_with_token(self):
        """Test getting HTTPS URL with token."""
        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git',
                           git_token='test_token')
        https_url = repo.get_https_url(with_token=True)

        assert https_url == 'https://test_token@github.com/user/repo.git'

    def test_get_https_url_with_port(self):
        """Test getting HTTPS URL with port."""
        repo = git.GitRepo(repo_url='ssh://git@gitlab.com:2222/user/repo.git')
        https_url = repo.get_https_url()

        assert https_url == 'https://gitlab.com:2222/user/repo.git'

    def test_get_https_url_remove_git_suffix(self):
        """Test getting HTTPS URL removes .git suffix from path."""
        repo = git.GitRepo(repo_url='https://github.com/user/repo.git.git')
        https_url = repo.get_https_url()

        assert https_url == 'https://github.com/user/repo.git'

    def test_get_ssh_url_basic(self):
        """Test getting SSH URL."""
        repo = git.GitRepo(repo_url='https://github.com/user/repo.git')
        ssh_url = repo.get_ssh_url()

        assert ssh_url == 'ssh://git@github.com/user/repo.git'

    def test_get_ssh_url_with_user(self):
        """Test getting SSH URL with existing user."""
        repo = git.GitRepo(repo_url='ssh://myuser@github.com/user/repo.git')
        ssh_url = repo.get_ssh_url()

        assert ssh_url == 'ssh://myuser@github.com/user/repo.git'

    def test_get_ssh_url_with_port(self):
        """Test getting SSH URL with port."""
        repo = git.GitRepo(repo_url='https://gitlab.com:8080/user/repo.git')
        ssh_url = repo.get_ssh_url()

        assert ssh_url == 'ssh://git@gitlab.com:8080/user/repo.git'

    def test_get_ssh_url_remove_git_suffix(self):
        """Test getting SSH URL removes .git suffix from path."""
        repo = git.GitRepo(repo_url='https://github.com/user/repo.git.git')
        ssh_url = repo.get_ssh_url()

        assert ssh_url == 'ssh://git@github.com/user/repo.git'

    @patch('requests.get')
    def test_get_repo_clone_info_public_access(self, mock_get):
        """Test getting clone info for public repository."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git')
        clone_info = repo.get_repo_clone_info()

        assert clone_info.url == 'https://github.com/user/repo.git'
        assert clone_info.token is None
        assert clone_info.ssh_key is None
        assert clone_info.envs is None

    @patch('requests.get')
    @patch('git.cmd.Git')
    def test_get_repo_clone_info_token_auth(self, mock_git_cmd, mock_get):
        """Test getting clone info with token authentication."""
        # Mock public access failure
        mock_get.side_effect = Exception('Public access failed')

        # Mock token auth success
        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        mock_git_instance.ls_remote.return_value = 'mock_output'

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git',
                           git_token='test_token')
        clone_info = repo.get_repo_clone_info()

        assert clone_info.url == 'https://github.com/user/repo.git'
        assert clone_info.token == 'test_token'
        assert clone_info.ssh_key is None

    @patch('requests.get')
    @patch('git.cmd.Git')
    def test_get_repo_clone_info_token_auth_failure(self, mock_git_cmd,
                                                    mock_get):
        """Test getting clone info with token authentication failure."""
        # Mock public access failure
        mock_get.side_effect = Exception('Public access failed')

        # Mock token auth failure
        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        mock_git_instance.ls_remote.side_effect = Exception('Token auth failed')

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git',
                           git_token='test_token')

        with pytest.raises(exceptions.GitError,
                           match='Failed to access repository'):
            repo.get_repo_clone_info()

    @patch('requests.get')
    @patch('git.cmd.Git')
    @patch.object(git.GitRepo, '_get_ssh_key_info')
    def test_get_repo_clone_info_ssh_auth(self, mock_get_ssh_key_info,
                                          mock_git_cmd, mock_get):
        """Test getting clone info with SSH authentication."""
        # Mock public access failure
        mock_get.side_effect = Exception('Public access failed')

        # Mock SSH key info
        mock_get_ssh_key_info.return_value = ('/path/to/key', 'ssh_key_content')

        # Mock SSH auth success
        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        mock_git_instance.ls_remote.return_value = 'mock_output'

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')
        clone_info = repo.get_repo_clone_info()

        assert clone_info.url == 'ssh://git@github.com/user/repo.git'
        assert clone_info.token is None
        assert clone_info.ssh_key == 'ssh_key_content'
        assert clone_info.envs is not None

    @patch('requests.get')
    def test_get_repo_clone_info_no_auth_methods(self, mock_get):
        """Test getting clone info when no authentication methods are available."""
        # Mock public access failure
        mock_get.side_effect = Exception('Public access failed')

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git')

        with pytest.raises(exceptions.GitError,
                           match='Failed to access repository'):
            repo.get_repo_clone_info()

    @patch('requests.get')
    @patch.object(git.GitRepo, '_get_ssh_key_info')
    def test_get_repo_clone_info_ssh_no_keys_found(self, mock_get_ssh_key_info,
                                                   mock_get):
        """Test getting clone info when SSH is used but no keys are found."""
        # Mock public access failure
        mock_get.side_effect = Exception('Public access failed')

        # Mock SSH key info to return None (no keys found)
        mock_get_ssh_key_info.return_value = None

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')

        with pytest.raises(exceptions.GitError, match='No SSH keys found'):
            repo.get_repo_clone_info()

    @patch('requests.get')
    @patch.object(git.GitRepo, '_get_ssh_key_info')
    @patch('git.cmd.Git')
    def test_get_repo_clone_info_ssh_auth_exception(self, mock_git_cmd,
                                                    mock_get_ssh_key_info,
                                                    mock_get):
        """Test getting clone info when SSH authentication raises exception."""
        # Mock public access failure
        mock_get.side_effect = Exception('Public access failed')

        # Mock SSH key info
        mock_get_ssh_key_info.return_value = ('/path/to/key', 'ssh_key_content')

        # Mock SSH auth to raise exception
        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        mock_git_instance.ls_remote.side_effect = Exception('SSH auth failed')

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')

        with pytest.raises(
                exceptions.GitError,
                match='Failed to access repository.*SSH key authentication'):
            repo.get_repo_clone_info()

    @patch('os.path.exists')
    @patch('builtins.open',
           new_callable=mock.mock_open,
           read_data='Host github.com\n    IdentityFile ~/.ssh/id_rsa\n')
    @patch('os.path.expanduser')
    def test_parse_ssh_config_with_paramiko(self, mock_expanduser, mock_open,
                                            mock_exists):
        """Test parsing SSH config with paramiko."""
        mock_exists.return_value = True
        mock_expanduser.side_effect = lambda path: path.replace(
            '~', '/home/user')

        with patch('paramiko.SSHConfig') as mock_ssh_config:
            mock_config_instance = MagicMock()
            mock_ssh_config.return_value = mock_config_instance
            mock_config_instance.lookup.return_value = {
                'identityfile': ['/home/user/.ssh/id_rsa']
            }

            with patch('os.path.exists', return_value=True):
                repo = git.GitRepo(
                    repo_url='ssh://git@github.com/user/repo.git')
                result = repo._parse_ssh_config()

                assert result == '/home/user/.ssh/id_rsa'

    @patch('os.path.exists')
    @patch('builtins.open',
           new_callable=mock.mock_open,
           read_data='Host github.com\n    IdentityFile ~/.ssh/id_rsa\n')
    @patch('os.path.expanduser')
    def test_parse_ssh_config_single_identity_file(self, mock_expanduser,
                                                   mock_open, mock_exists):
        """Test parsing SSH config with single identity file string."""
        mock_exists.return_value = True
        mock_expanduser.side_effect = lambda path: path.replace(
            '~', '/home/user')

        with patch('paramiko.SSHConfig') as mock_ssh_config:
            mock_config_instance = MagicMock()
            mock_ssh_config.return_value = mock_config_instance
            # Return single string instead of list
            mock_config_instance.lookup.return_value = {
                'identityfile': '/home/user/.ssh/id_rsa'
            }

            with patch('os.path.exists', return_value=True):
                repo = git.GitRepo(
                    repo_url='ssh://git@github.com/user/repo.git')
                result = repo._parse_ssh_config()

                assert result == '/home/user/.ssh/id_rsa'

    @patch('os.path.exists')
    @patch('os.path.expanduser')
    def test_parse_ssh_config_no_file(self, mock_expanduser, mock_exists):
        """Test parsing SSH config when file doesn't exist."""
        mock_exists.return_value = False
        mock_expanduser.side_effect = lambda path: path.replace(
            '~', '/home/user')

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')
        result = repo._parse_ssh_config()

        assert result is None

    @patch('os.path.exists')
    @patch('builtins.open',
           new_callable=mock.mock_open,
           read_data='Host github.com\n    IdentityFile ~/.ssh/id_rsa\n')
    @patch('os.path.expanduser')
    def test_parse_ssh_config_paramiko_import_error(self, mock_expanduser,
                                                    mock_open, mock_exists):
        """Test parsing SSH config when paramiko is not available."""
        mock_exists.return_value = True
        mock_expanduser.side_effect = lambda path: path.replace(
            '~', '/home/user')

        with patch('paramiko.SSHConfig',
                   side_effect=ImportError('paramiko not available')):
            repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')
            result = repo._parse_ssh_config()

            assert result is None

    @patch('os.path.exists')
    @patch('builtins.open',
           new_callable=mock.mock_open,
           read_data='Host github.com\n    IdentityFile ~/.ssh/id_rsa\n')
    @patch('os.path.expanduser')
    def test_parse_ssh_config_parsing_error(self, mock_expanduser, mock_open,
                                            mock_exists):
        """Test parsing SSH config when parsing fails."""
        mock_exists.return_value = True
        mock_expanduser.side_effect = lambda path: path.replace(
            '~', '/home/user')

        with patch('paramiko.SSHConfig') as mock_ssh_config:
            mock_config_instance = MagicMock()
            mock_ssh_config.return_value = mock_config_instance
            mock_config_instance.lookup.side_effect = Exception(
                'Parsing failed')

            repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')
            result = repo._parse_ssh_config()

            assert result is None

    @patch('os.path.exists')
    @patch('builtins.open',
           new_callable=mock.mock_open,
           read_data='Host github.com\n    IdentityFile ~/.ssh/id_rsa\n')
    @patch('os.path.expanduser')
    def test_parse_ssh_config_no_valid_keys(self, mock_expanduser, mock_open,
                                            mock_exists):
        """Test parsing SSH config when no valid keys are found."""
        mock_expanduser.side_effect = lambda path: path.replace(
            '~', '/home/user')

        def mock_exists_side_effect(path):
            # SSH config exists but identity file doesn't
            if path.endswith('/.ssh/config'):
                return True
            return False

        mock_exists.side_effect = mock_exists_side_effect

        with patch('paramiko.SSHConfig') as mock_ssh_config:
            mock_config_instance = MagicMock()
            mock_ssh_config.return_value = mock_config_instance
            mock_config_instance.lookup.return_value = {
                'identityfile': ['/home/user/.ssh/id_rsa']
            }

            repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')
            result = repo._parse_ssh_config()

            assert result is None

    @patch('os.path.exists')
    @patch('builtins.open',
           new_callable=mock.mock_open,
           read_data=
           '-----BEGIN PRIVATE KEY-----\nkey_content\n-----END PRIVATE KEY-----'
          )
    @patch('os.stat')
    @patch('os.path.expanduser')
    def test_get_ssh_key_info_provided_key(self, mock_expanduser, mock_stat,
                                           mock_open, mock_exists):
        """Test getting SSH key info with provided key path."""
        mock_exists.return_value = True
        mock_stat.return_value = MagicMock()
        mock_stat.return_value.st_mode = 0o600
        mock_expanduser.return_value = '/home/user/.ssh/id_rsa'

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git',
                           git_ssh_key_path='~/.ssh/id_rsa')
        result = repo._get_ssh_key_info()

        assert result[0] == '/home/user/.ssh/id_rsa'  # expanded path
        assert '-----BEGIN PRIVATE KEY-----' in result[1]

    @patch('os.path.exists')
    @patch('builtins.open',
           new_callable=mock.mock_open,
           read_data=
           '-----BEGIN PRIVATE KEY-----\nkey_content\n-----END PRIVATE KEY-----'
          )
    @patch('os.stat')
    @patch('os.path.expanduser')
    def test_get_ssh_key_info_insecure_permissions(self, mock_expanduser,
                                                   mock_stat, mock_open,
                                                   mock_exists):
        """Test getting SSH key info with insecure permissions."""
        mock_exists.return_value = True
        mock_stat.return_value = MagicMock()
        mock_stat.return_value.st_mode = 0o644  # Insecure permissions
        mock_expanduser.return_value = '/home/user/.ssh/id_rsa'

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git',
                           git_ssh_key_path='~/.ssh/id_rsa')
        result = repo._get_ssh_key_info()

        assert result[0] == '/home/user/.ssh/id_rsa'  # expanded path
        assert '-----BEGIN PRIVATE KEY-----' in result[1]

    @patch('os.path.exists')
    @patch('os.path.expanduser')
    def test_get_ssh_key_info_provided_key_not_found(self, mock_expanduser,
                                                     mock_exists):
        """Test getting SSH key info when provided key doesn't exist."""
        mock_exists.return_value = False
        mock_expanduser.return_value = '/home/user/.ssh/missing_key'

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git',
                           git_ssh_key_path='~/.ssh/missing_key')

        with pytest.raises(exceptions.GitError, match='SSH key not found'):
            repo._get_ssh_key_info()

    @patch('os.path.exists')
    @patch('builtins.open',
           new_callable=mock.mock_open,
           read_data='invalid key content')
    @patch('os.stat')
    @patch('os.path.expanduser')
    def test_get_ssh_key_info_invalid_key(self, mock_expanduser, mock_stat,
                                          mock_open, mock_exists):
        """Test getting SSH key info with invalid key content."""
        mock_exists.return_value = True
        mock_stat.return_value = MagicMock()
        mock_stat.return_value.st_mode = 0o600
        mock_expanduser.return_value = '/home/user/.ssh/id_rsa'

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git',
                           git_ssh_key_path='~/.ssh/id_rsa')

        with pytest.raises(exceptions.GitError, match='SSH key.*is invalid'):
            repo._get_ssh_key_info()

    @patch('os.path.exists')
    @patch('builtins.open', side_effect=Exception('Cannot read file'))
    @patch('os.stat')
    @patch('os.path.expanduser')
    def test_get_ssh_key_info_read_error(self, mock_expanduser, mock_stat,
                                         mock_open, mock_exists):
        """Test getting SSH key info when file cannot be read."""
        mock_exists.return_value = True
        mock_stat.return_value = MagicMock()
        mock_stat.return_value.st_mode = 0o600
        mock_expanduser.return_value = '/home/user/.ssh/id_rsa'

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git',
                           git_ssh_key_path='~/.ssh/id_rsa')

        with pytest.raises(exceptions.GitError,
                           match='Validate provided SSH key error'):
            repo._get_ssh_key_info()

    @patch.object(git.GitRepo, '_parse_ssh_config')
    @patch('os.path.exists')
    @patch('builtins.open',
           new_callable=mock.mock_open,
           read_data=
           '-----BEGIN PRIVATE KEY-----\nconfig_key\n-----END PRIVATE KEY-----')
    def test_get_ssh_key_info_from_config(self, mock_open, mock_exists,
                                          mock_parse_ssh_config):
        """Test getting SSH key info from SSH config."""
        mock_parse_ssh_config.return_value = '/home/user/.ssh/config_key'
        mock_exists.return_value = True

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')
        result = repo._get_ssh_key_info()

        assert result[0] == '/home/user/.ssh/config_key'
        assert '-----BEGIN PRIVATE KEY-----' in result[1]

    @patch.object(git.GitRepo, '_parse_ssh_config')
    @patch('os.path.exists')
    @patch('builtins.open',
           new_callable=mock.mock_open,
           read_data=
           '-----BEGIN PRIVATE KEY-----\ndefault_key\n-----END PRIVATE KEY-----'
          )
    @patch('os.path.expanduser')
    @patch('os.stat')
    def test_get_ssh_key_info_default_keys(self, mock_stat, mock_expanduser,
                                           mock_open, mock_exists,
                                           mock_parse_ssh_config):
        """Test getting SSH key info from default locations."""
        mock_parse_ssh_config.return_value = None
        mock_expanduser.return_value = '/home/user/.ssh'
        mock_stat.return_value = MagicMock()
        mock_stat.return_value.st_mode = 0o600

        def mock_exists_side_effect(path):
            return path.endswith('/.ssh') or path.endswith(
                '/id_rsa') or path.endswith('/id_rsa.pub')

        mock_exists.side_effect = mock_exists_side_effect

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')
        result = repo._get_ssh_key_info()

        assert result is not None
        assert result[0].endswith('/.ssh/id_rsa')
        assert '-----BEGIN PRIVATE KEY-----' in result[1]

    @patch.object(git.GitRepo, '_parse_ssh_config')
    @patch('os.path.exists')
    @patch('builtins.open',
           new_callable=mock.mock_open,
           read_data='invalid key content')
    @patch('os.path.expanduser')
    @patch('os.stat')
    def test_get_ssh_key_info_default_keys_invalid_content(
            self, mock_stat, mock_expanduser, mock_open, mock_exists,
            mock_parse_ssh_config):
        """Test getting SSH key info from default locations with invalid keys."""
        mock_parse_ssh_config.return_value = None
        mock_expanduser.return_value = '/home/user/.ssh'
        mock_stat.return_value = MagicMock()
        mock_stat.return_value.st_mode = 0o600

        def mock_exists_side_effect(path):
            return path.endswith('/.ssh') or path.endswith(
                '/id_rsa') or path.endswith('/id_rsa.pub')

        mock_exists.side_effect = mock_exists_side_effect

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')
        result = repo._get_ssh_key_info()

        # Should return None since all keys are invalid
        assert result is None

    @patch.object(git.GitRepo, '_parse_ssh_config')
    @patch('os.path.exists')
    @patch('builtins.open', side_effect=Exception('Cannot read file'))
    @patch('os.path.expanduser')
    @patch('os.stat')
    def test_get_ssh_key_info_default_keys_read_error(self, mock_stat,
                                                      mock_expanduser,
                                                      mock_open, mock_exists,
                                                      mock_parse_ssh_config):
        """Test getting SSH key info from default locations with read errors."""
        mock_parse_ssh_config.return_value = None
        mock_expanduser.return_value = '/home/user/.ssh'
        mock_stat.return_value = MagicMock()
        mock_stat.return_value.st_mode = 0o600

        def mock_exists_side_effect(path):
            return path.endswith('/.ssh') or path.endswith(
                '/id_rsa') or path.endswith('/id_rsa.pub')

        mock_exists.side_effect = mock_exists_side_effect

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')
        result = repo._get_ssh_key_info()

        # Should return None since all keys have read errors
        assert result is None

    @patch.object(git.GitRepo, '_parse_ssh_config')
    @patch('os.path.exists')
    @patch('os.path.expanduser')
    def test_get_ssh_key_info_no_keys_found(self, mock_expanduser, mock_exists,
                                            mock_parse_ssh_config):
        """Test getting SSH key info when no keys are found."""
        mock_parse_ssh_config.return_value = None
        mock_exists.return_value = False
        mock_expanduser.return_value = '/home/user/.ssh'

        repo = git.GitRepo(repo_url='ssh://git@github.com/user/repo.git')
        result = repo._get_ssh_key_info()

        assert result is None

    @patch.object(git.GitRepo, 'get_repo_clone_info')
    @patch('git.cmd.Git')
    def test_get_ref_type_branch(self, mock_git_cmd, mock_get_repo_clone_info):
        """Test get_ref_type returns git.GitRefType.BRANCH for branch."""
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/user/repo.git'
        mock_clone_info.envs = None
        mock_get_repo_clone_info.return_value = mock_clone_info

        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        mock_git_instance.ls_remote.return_value = 'abcdef123456\trefs/heads/main\n'

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git',
                           ref='main')
        result = repo.get_ref_type()

        assert result == git.GitRefType.BRANCH

    @patch.object(git.GitRepo, 'get_repo_clone_info')
    @patch('git.cmd.Git')
    def test_get_ref_type_tag(self, mock_git_cmd, mock_get_repo_clone_info):
        """Test get_ref_type returns git.GitRefType.TAG for tag."""
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/user/repo.git'
        mock_clone_info.envs = None
        mock_get_repo_clone_info.return_value = mock_clone_info

        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        mock_git_instance.ls_remote.return_value = 'abcdef123456\trefs/tags/v1.0.0\n'

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git',
                           ref='v1.0.0')
        result = repo.get_ref_type()

        assert result == git.GitRefType.TAG

    @patch.object(git.GitRepo, 'get_repo_clone_info')
    @patch('git.cmd.Git')
    def test_get_ref_type_commit_exact_match(self, mock_git_cmd,
                                             mock_get_repo_clone_info):
        """Test get_ref_type returns git.GitRefType.COMMIT for exact commit hash match."""
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/user/repo.git'
        mock_clone_info.envs = None
        mock_get_repo_clone_info.return_value = mock_clone_info

        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        mock_git_instance.ls_remote.return_value = 'abcdef123456\trefs/heads/main\n'

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git',
                           ref='abcdef123456')
        result = repo.get_ref_type()

        assert result == git.GitRefType.COMMIT

    @patch.object(git.GitRepo, 'get_repo_clone_info')
    @patch('git.cmd.Git')
    def test_get_ref_type_commit_prefix_match(self, mock_git_cmd,
                                              mock_get_repo_clone_info):
        """Test get_ref_type returns git.GitRefType.COMMIT for commit hash prefix match."""
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/user/repo.git'
        mock_clone_info.envs = None
        mock_get_repo_clone_info.return_value = mock_clone_info

        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        mock_git_instance.ls_remote.return_value = 'abcdef123456\trefs/heads/main\n'

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git',
                           ref='abcdef')
        result = repo.get_ref_type()

        assert result == git.GitRefType.COMMIT

    @patch.object(git.GitRepo, 'get_repo_clone_info')
    @patch('git.cmd.Git')
    def test_get_ref_type_ambiguous_prefix(self, mock_git_cmd,
                                           mock_get_repo_clone_info):
        """Test get_ref_type raises error for ambiguous commit hash prefix."""
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/user/repo.git'
        mock_clone_info.envs = None
        mock_get_repo_clone_info.return_value = mock_clone_info

        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        mock_git_instance.ls_remote.return_value = 'abcdef123456\trefs/heads/main\nabcdef789012\trefs/heads/develop\n'

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git',
                           ref='abcdef')

        with pytest.raises(exceptions.GitError, match='Ambiguous commit hash'):
            repo.get_ref_type()

    @patch.object(git.GitRepo, 'get_repo_clone_info')
    @patch('git.cmd.Git')
    def test_get_ref_type_invalid_ref(self, mock_git_cmd,
                                      mock_get_repo_clone_info):
        """Test get_ref_type raises error for invalid reference."""
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/user/repo.git'
        mock_clone_info.envs = None
        mock_get_repo_clone_info.return_value = mock_clone_info

        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        mock_git_instance.ls_remote.return_value = 'abcdef123456\trefs/heads/main\n'

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git',
                           ref='invalid-ref')

        with pytest.raises(exceptions.GitError,
                           match='Git reference.*not found'):
            repo.get_ref_type()

    @patch.object(git.GitRepo, 'get_repo_clone_info')
    @patch('git.cmd.Git')
    def test_get_ref_type_git_command_error(self, mock_git_cmd,
                                            mock_get_repo_clone_info):
        """Test get_ref_type handles git command errors."""
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/user/repo.git'
        mock_clone_info.envs = None
        mock_get_repo_clone_info.return_value = mock_clone_info

        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance

        # Mock git.exc.GitCommandError for proper error handling
        from git import exc as git_exc
        mock_git_instance.ls_remote.side_effect = git_exc.GitCommandError(
            'ls-remote', 128)

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git',
                           ref='main')

        with pytest.raises(exceptions.GitError,
                           match='Failed to check repository'):
            repo.get_ref_type()

    @patch.object(git.GitRepo, 'get_repo_clone_info')
    @patch('git.cmd.Git')
    def test_get_ref_type_with_envs(self, mock_git_cmd,
                                    mock_get_repo_clone_info):
        """Test get_ref_type updates environment variables correctly."""
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/user/repo.git'
        mock_clone_info.envs = {'GIT_SSH_COMMAND': 'ssh -i key'}
        mock_get_repo_clone_info.return_value = mock_clone_info

        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        mock_git_instance.ls_remote.return_value = 'abcdef123456\trefs/heads/main\n'

        repo = git.GitRepo(repo_url='https://github.com/user/repo.git',
                           ref='main')
        result = repo.get_ref_type()

        assert result == git.GitRefType.BRANCH
        # Verify environment variables were updated
        mock_git_instance.update_environment.assert_called_once_with(
            **mock_clone_info.envs)

    @patch.object(git.GitRepo, 'get_repo_clone_info')
    @patch('git.cmd.Git')
    def test_get_ref_type_commit_not_in_refs(self, mock_git_cmd,
                                             mock_get_repo_clone_info):
        """Test get_ref_type handles commit not found in refs."""
        mock_clone_info = MagicMock()
        mock_clone_info.url = 'https://github.com/user/repo.git'
        mock_clone_info.envs = None
        mock_get_repo_clone_info.return_value = mock_clone_info

        mock_git_instance = MagicMock()
        mock_git_cmd.return_value = mock_git_instance
        # Return refs that don't contain the commit hash
        mock_git_instance.ls_remote.return_value = 'aaaa111111\trefs/heads/main\nbbbb222222\trefs/heads/develop\n'

        # Test with a hex-like string that's not in refs
        repo = git.GitRepo(repo_url='https://github.com/user/repo.git',
                           ref='cccc333333')
        result = repo.get_ref_type()

        # Should return git.GitRefType.COMMIT and log warning
        assert result == git.GitRefType.COMMIT
