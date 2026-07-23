"""Tests for sky.skylet.runtime_utils."""
import os

from sky.skylet import constants
from sky.skylet import runtime_utils

_RUNTIME_DIR_KEY = constants.SKY_RUNTIME_DIR_ENV_VAR_KEY


class TestExpanduser:
    """Tests for runtime_utils.expanduser."""

    def test_no_runtime_dir_matches_os_expanduser(self, monkeypatch):
        monkeypatch.delenv(_RUNTIME_DIR_KEY, raising=False)
        for path in ('~', '~/.sky/state.db', '/abs/path', 'relative/path'):
            assert runtime_utils.expanduser(path) == os.path.expanduser(path)

    def test_runtime_dir_roots_tilde(self, monkeypatch, tmp_path):
        monkeypatch.setenv(_RUNTIME_DIR_KEY, str(tmp_path))
        assert runtime_utils.expanduser('~') == str(tmp_path)
        assert runtime_utils.expanduser('~/.sky/state.db') == str(
            tmp_path / '.sky/state.db')

    def test_runtime_dir_leaves_other_paths_alone(self, monkeypatch, tmp_path):
        monkeypatch.setenv(_RUNTIME_DIR_KEY, str(tmp_path))
        assert runtime_utils.expanduser('/abs/path') == '/abs/path'
        assert runtime_utils.expanduser('rel/path') == 'rel/path'
        # '~user' forms are not rooted at the runtime dir.
        assert runtime_utils.expanduser('~someuser/x') == os.path.expanduser(
            '~someuser/x')

    def test_runtime_dir_itself_is_expanded(self, monkeypatch):
        monkeypatch.setenv(_RUNTIME_DIR_KEY, '~/runtime-a')
        assert runtime_utils.expanduser('~/.sky/x') == os.path.join(
            os.path.expanduser('~/runtime-a'), '.sky/x')
