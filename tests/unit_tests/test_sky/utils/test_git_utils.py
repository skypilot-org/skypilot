"""Tests for git utility functions in common_utils."""
import subprocess
from unittest import mock

from sky.utils import common_utils


class TestGetGitCommit:
    """Tests for get_git_commit."""

    def test_returns_commit_hash_in_git_repo(self, tmp_path):
        """Should return a 40-char hex commit hash in a valid git repo."""
        subprocess.run(['git', 'init'],
                       cwd=tmp_path,
                       check=True,
                       capture_output=True)
        subprocess.run(['git', 'commit', '--allow-empty', '-m', 'init'],
                       cwd=tmp_path,
                       check=True,
                       capture_output=True)
        result = common_utils.get_git_commit(str(tmp_path))
        assert result is not None
        assert len(result) == 40
        assert all(c in '0123456789abcdef' for c in result)

    def test_returns_none_outside_git_repo(self, tmp_path):
        """Should return None when path is not a git repo."""
        result = common_utils.get_git_commit(str(tmp_path))
        assert result is None

    def test_returns_none_when_git_not_installed(self, tmp_path):
        """Should return None (not raise) when git binary is missing."""
        with mock.patch('subprocess.run', side_effect=FileNotFoundError):
            result = common_utils.get_git_commit(str(tmp_path))
        assert result is None


class TestGetGitDirty:
    """Tests for get_git_dirty."""

    def test_clean_repo(self, tmp_path):
        """Should return False for a repo with no uncommitted changes."""
        subprocess.run(['git', 'init'],
                       cwd=tmp_path,
                       check=True,
                       capture_output=True)
        subprocess.run(['git', 'commit', '--allow-empty', '-m', 'init'],
                       cwd=tmp_path,
                       check=True,
                       capture_output=True)
        result = common_utils.get_git_dirty(str(tmp_path))
        assert result is False

    def test_dirty_repo_with_modified_file(self, tmp_path):
        """Should return True when there are uncommitted changes."""
        subprocess.run(['git', 'init'],
                       cwd=tmp_path,
                       check=True,
                       capture_output=True)
        (tmp_path / 'file.txt').write_text('hello')
        subprocess.run(['git', 'add', '.'],
                       cwd=tmp_path,
                       check=True,
                       capture_output=True)
        subprocess.run(['git', 'commit', '-m', 'add file'],
                       cwd=tmp_path,
                       check=True,
                       capture_output=True)
        (tmp_path / 'file.txt').write_text('modified')
        result = common_utils.get_git_dirty(str(tmp_path))
        assert result is True

    def test_dirty_repo_with_untracked_file(self, tmp_path):
        """Should return True when there are untracked files."""
        subprocess.run(['git', 'init'],
                       cwd=tmp_path,
                       check=True,
                       capture_output=True)
        subprocess.run(['git', 'commit', '--allow-empty', '-m', 'init'],
                       cwd=tmp_path,
                       check=True,
                       capture_output=True)
        (tmp_path / 'untracked.txt').write_text('new')
        result = common_utils.get_git_dirty(str(tmp_path))
        assert result is True

    def test_returns_none_outside_git_repo(self, tmp_path):
        """Should return None when path is not a git repo."""
        result = common_utils.get_git_dirty(str(tmp_path))
        assert result is None

    def test_returns_none_when_git_not_installed(self, tmp_path):
        """Should return None (not raise) when git binary is missing."""
        with mock.patch('subprocess.run', side_effect=FileNotFoundError):
            result = common_utils.get_git_dirty(str(tmp_path))
        assert result is None
