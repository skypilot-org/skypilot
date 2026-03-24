"""Tests for git metadata capture in Task.expand_and_validate_workdir."""
import subprocess
from unittest import mock

from sky import task as task_lib


def _init_git_repo(path):
    """Initialize a git repo with one commit, return the commit hash."""
    subprocess.run(['git', 'init'], cwd=path, check=True, capture_output=True)
    subprocess.run(['git', 'commit', '--allow-empty', '-m', 'init'],
                   cwd=path,
                   check=True,
                   capture_output=True)
    result = subprocess.run(['git', 'rev-parse', 'HEAD'],
                            cwd=path,
                            check=True,
                            capture_output=True,
                            text=True)
    return result.stdout.strip()


class TestExpandAndValidateWorkdirGitMetadata:
    """Tests for git metadata capture in expand_and_validate_workdir."""

    # pylint: disable=protected-access

    def test_workdir_in_git_repo_captures_commit_and_dirty(self, tmp_path):
        """With workdir set to a git repo, should capture commit and dirty."""
        commit_hash = _init_git_repo(tmp_path)
        t = task_lib.Task(workdir=str(tmp_path))
        t.expand_and_validate_workdir()
        assert t._metadata['git_commit'] == commit_hash
        assert t._metadata['git_dirty'] is False

    def test_workdir_dirty_repo(self, tmp_path):
        """With workdir in a dirty git repo, git_dirty should be True."""
        _init_git_repo(tmp_path)
        (tmp_path / 'file.txt').write_text('uncommitted')
        t = task_lib.Task(workdir=str(tmp_path))
        t.expand_and_validate_workdir()
        assert t._metadata['git_dirty'] is True

    def test_yaml_no_workdir_uses_cwd(self, tmp_path):
        """YAML-based task with no workdir should use CWD for git info."""
        commit_hash = _init_git_repo(tmp_path)
        t = task_lib.Task()
        t._user_specified_yaml = 'name: test'  # Simulate YAML-based task
        with mock.patch('os.getcwd', return_value=str(tmp_path)):
            t.expand_and_validate_workdir()
        assert t._metadata['git_commit'] == commit_hash
        assert t._metadata['git_dirty'] is False

    def test_inline_command_no_yaml_no_metadata(self):
        """Inline command with no YAML and no workdir: no git metadata."""
        t = task_lib.Task(run='echo hello')
        t.expand_and_validate_workdir()
        assert 'git_commit' not in t._metadata
        assert 'git_dirty' not in t._metadata

    def test_dict_workdir_uses_ref(self):
        """Dict workdir should use 'ref' as git_commit, skip dirty."""
        t = task_lib.Task(workdir={
            'git_url': 'https://example.com/repo.git',
            'ref': 'abc123'
        })
        t.expand_and_validate_workdir()
        assert t._metadata['git_commit'] == 'abc123'
        assert 'git_dirty' not in t._metadata

    def test_server_revalidation_preserves_metadata(self, tmp_path):
        """Server-side re-validation should not overwrite client metadata."""
        t = task_lib.Task(workdir=str(tmp_path))
        # Simulate client already set metadata
        t._metadata['git_commit'] = 'client-side-hash'
        t._metadata['git_dirty'] = True
        # Server re-validates; workdir may not exist on server
        # but metadata should be preserved
        t.workdir = None  # Server might clear this
        t.expand_and_validate_workdir()
        assert t._metadata['git_commit'] == 'client-side-hash'
        assert t._metadata['git_dirty'] is True

    def test_workdir_not_git_repo(self, tmp_path):
        """Workdir that is not a git repo: git_commit and git_dirty are None."""
        t = task_lib.Task(workdir=str(tmp_path))
        t.expand_and_validate_workdir()
        assert t._metadata['git_commit'] is None
        assert t._metadata['git_dirty'] is None
