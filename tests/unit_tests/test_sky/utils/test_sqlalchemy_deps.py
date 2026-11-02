"""SQLAlchemy is server-only; helpers must fail clearly when it is missing."""
import ast
from pathlib import Path

import pytest

from sky.adaptors import common as adaptors_common
from sky.utils.db import sqlalchemy_deps

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CLIENT_SAFE_MODULES = (
    'sky/utils/db/db_utils.py',
    'sky/server/config_db.py',
    'sky/global_user_state.py',
    'sky/jobs/state.py',
    'sky/serve/serve_state.py',
)


def test_require_sqlalchemy_raises_install_hint(monkeypatch):
    """Missing SQLAlchemy should raise the install hint, not a bare ImportError."""
    sqlalchemy_deps.sqlalchemy_available.cache_clear()
    monkeypatch.setattr(sqlalchemy_deps, 'sqlalchemy_available', lambda: False)
    with pytest.raises(ImportError, match=r'skypilot\[server\]'):
        sqlalchemy_deps.require_sqlalchemy()


def test_sqlalchemy_available_is_cached(monkeypatch):
    """Version/metadata lookup should run once (lru_cache)."""
    sqlalchemy_deps.sqlalchemy_available.cache_clear()
    calls = {'n': 0}
    real_can_import = adaptors_common.can_import_modules

    def counting_can_import(modules):
        calls['n'] += 1
        return real_can_import(modules)

    monkeypatch.setattr(adaptors_common, 'can_import_modules',
                        counting_can_import)
    assert sqlalchemy_deps.sqlalchemy_available() is True
    assert sqlalchemy_deps.sqlalchemy_available() is True
    assert calls['n'] == 1
    sqlalchemy_deps.sqlalchemy_available.cache_clear()


def _top_level_imports_sqlalchemy(path: Path) -> bool:
    """Return True if module body has an unconditional sqlalchemy import."""
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'sqlalchemy' or alias.name.startswith(
                        'sqlalchemy.'):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            if mod == 'sqlalchemy' or mod.startswith('sqlalchemy.'):
                return True
    return False


@pytest.mark.parametrize('relpath', _CLIENT_SAFE_MODULES)
def test_client_safe_modules_have_no_top_level_sqlalchemy_import(relpath):
    """Regression: client-imported modules must not ``import sqlalchemy`` at
    module top level (LazyImport / sqlalchemy_available gate only).
    """
    path = _REPO_ROOT / relpath
    assert path.is_file(), path
    assert not _top_level_imports_sqlalchemy(path), (
        f'{relpath} has a top-level sqlalchemy import; use LazyImport or '
        'gate behind sqlalchemy_available() so client installs stay importable'
    )
