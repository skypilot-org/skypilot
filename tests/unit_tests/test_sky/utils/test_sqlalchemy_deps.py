"""SQLAlchemy is server-only; helpers must fail clearly when it is missing."""
import ast
import importlib
from pathlib import Path
import subprocess
import sys
import textwrap

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
        'gate behind sqlalchemy_available() so client installs stay importable')


@pytest.mark.parametrize('facade_mod, db_mod', [
    ('sky.global_user_state', 'sky.global_user_state_db'),
    ('sky.jobs.state', 'sky.jobs.state_db'),
    ('sky.serve.serve_state', 'sky.serve.serve_state_db'),
])
def test_facade_reexports_match_db_all(facade_mod, db_mod):
    """Facade must bind every name in ``*_db.__all__`` (static re-export list).

    ``globals().update`` is invisible to mypy/pylint, so facades use explicit
    assigns; this test catches drift when ``__all__`` gains a name.
    """
    facade = importlib.import_module(facade_mod)
    db = importlib.import_module(db_mod)
    missing = [n for n in db.__all__ if not hasattr(facade, n)]
    assert not missing, f'{facade_mod} missing re-exports of {missing}'


# Prepended to subprocess scripts: hide the DB stack the way a client-only
# install lacks it. Runs in a fresh interpreter because this pytest process
# already imported sky and sqlalchemy.
_HIDE_DB_STACK = textwrap.dedent("""
    import sys

    BLOCKED = ('sqlalchemy', 'sqlalchemy_adapter', 'alembic', 'psycopg2',
               'asyncpg', 'aiosqlite', 'casbin', 'greenlet')

    class _Block:
        def find_spec(self, name, path=None, target=None):
            if name.split('.')[0] in BLOCKED:
                # ValueError: can_import_modules() treats it as absent,
                # while a real import still fails loudly.
                raise ValueError('client-only import of ' + name)

    sys.meta_path.insert(0, _Block())
""")


def _run_without_db_stack(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, '-c', _HIDE_DB_STACK + textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False)


def test_client_surface_imports_without_db_stack():
    """A client-only install (no DB stack) must import and run ``sky --help``.

    AST checks only cover a few facade files and direct ``sqlalchemy``
    imports; this catches any transitive import of the DB stack.
    """
    result = _run_without_db_stack("""
        import sky, sky.client.sdk
        from click.testing import CliRunner
        from sky.client.cli import command
        result = CliRunner().invoke(command.cli, ['--help'])
        assert result.exit_code == 0, result.output
    """)
    assert result.returncode == 0, result.stdout + result.stderr


def test_unavailable_database_manager_raises_install_hint():
    manager = sqlalchemy_deps.UnavailableDatabaseManager()
    with pytest.raises(ImportError, match=r'skypilot\[server\]'):
        manager.get_engine()


def test_client_db_api_calls_raise_install_hint():
    """On a client-only install, DB APIs raise the install hint instead of
    ``'NoneType' object has no attribute 'get_engine'``."""
    result = _run_without_db_stack("""
        from sky import global_user_state
        from sky.recipes import db as recipes_db

        for call in (
                lambda: global_user_state.get_handle_from_storage_name('x'),
                lambda: recipes_db.get_recipe('x'),
                global_user_state.initialize_and_get_db,
        ):
            try:
                call()
            except ImportError as e:
                assert 'skypilot[server]' in str(e), repr(e)
            else:
                raise AssertionError('expected the install hint')
    """)
    assert result.returncode == 0, result.stdout + result.stderr


def test_require_server_dependencies_checks_every_server_package(monkeypatch):
    """SQLAlchemy alone is not enough to start a server: any missing server
    package fails with the start message."""
    sqlalchemy_deps.require_server_dependencies()
    monkeypatch.setattr(sqlalchemy_deps, '_SERVER_MODULES',
                        ['sqlalchemy', 'sky_no_such_server_module'])
    with pytest.raises(ImportError) as e:
        sqlalchemy_deps.require_server_dependencies()
    assert str(e.value) == sqlalchemy_deps.SERVER_START_INSTALL_MSG


def test_server_start_without_db_stack_shows_only_the_install_hint():
    """Starting a local server on a client-only install fails with the start
    message alone, not chained under the expected connection failure."""
    result = _run_without_db_stack("""
        from sky import exceptions
        from sky.server import common
        from sky.utils.db import sqlalchemy_deps

        try:
            # The auto-start path starts the server while handling this.
            try:
                raise exceptions.ApiServerConnectionError('http://x')
            except exceptions.ApiServerConnectionError:
                common._start_api_server()
        except ImportError as e:
            assert str(e) == sqlalchemy_deps.SERVER_START_INSTALL_MSG, str(e)
            assert e.__suppress_context__, 'connection error would be shown'
        else:
            raise AssertionError('expected the install hint')
    """)
    assert result.returncode == 0, result.stdout + result.stderr
