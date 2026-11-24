"""Optional SQLAlchemy install helpers for client vs server installs.

SkyPilot's client/CLI package no longer depends on SQLAlchemy. The API server
and managed-jobs controller need the DB stack via ``skypilot[server]`` (or any
cloud extra, which already includes ``server_dependencies``).

Import this module from DB-backed modules so missing or too-old SQLAlchemy
raises a clear install hint instead of a bare ``ModuleNotFoundError`` or an
obscure 1.x API failure.
"""
import functools
import importlib.metadata

from packaging import version as version_lib

from sky.adaptors import common as adaptors_common

# Matches sky/setup_files/dependencies.py ``server_dependencies``.
_MIN_SQLALCHEMY_VERSION = version_lib.Version('2.0.16')

_INSTALL_HINT = 'Install with: pip install "skypilot[server]"'

SQLALCHEMY_INSTALL_MSG = (
    f'SkyPilot server dependencies are missing. {_INSTALL_HINT}')

SERVER_START_INSTALL_MSG = ('Failed to start SkyPilot API server due to '
                            f'missing dependencies. {_INSTALL_HINT}')

# Import names of the server-only packages in ``server_dependencies`` (see
# sky/setup_files/dependencies.py) that a client-only install lacks.
_SERVER_MODULES = [
    'sqlalchemy',
    'psycopg2',
    'aiosqlite',
    'asyncpg',
    'greenlet',
    'alembic',
    'casbin',
    'sqlalchemy_adapter',
]


@functools.lru_cache(maxsize=1)
def sqlalchemy_available() -> bool:
    """Return True if a supported SQLAlchemy (``>= 2.0.16``) is importable.

    Presence alone is not enough: a client env pinned to SQLAlchemy 1.4 must
    take the no-ORM path rather than building schemas that assume 2.x APIs.
    Uses package metadata so the check does not import SQLAlchemy itself.
    """
    if not adaptors_common.can_import_modules(['sqlalchemy']):
        return False
    try:
        installed = importlib.metadata.version('sqlalchemy')
    except importlib.metadata.PackageNotFoundError:
        return False
    try:
        return version_lib.Version(installed) >= _MIN_SQLALCHEMY_VERSION
    except version_lib.InvalidVersion:
        return False


def require_sqlalchemy() -> None:
    """Raise ImportError when SQLAlchemy is missing or too old."""
    if not sqlalchemy_available():
        raise ImportError(SQLALCHEMY_INSTALL_MSG)


def require_server_dependencies() -> None:
    """Raise ImportError when a package the API server needs is missing.

    Checked before starting an API server, which would otherwise exit on its
    first missing import and leave only a failed health check behind.
    """
    if not (sqlalchemy_available() and
            adaptors_common.can_import_modules(_SERVER_MODULES)):
        # `from None`: callers start the server while handling a failed
        # connection, which is expected here and only hides this cause.
        raise ImportError(SERVER_START_INSTALL_MSG) from None


class UnavailableDatabaseManager:
    """Stands in for a DatabaseManager on a client-only install.

    DB-backed modules bind this instead of None when SQLAlchemy is missing,
    so a DB API called on a client fails with the install hint rather than
    ``'NoneType' object has no attribute 'get_engine'``.
    """

    def __getattr__(self, name: str):
        raise ImportError(SQLALCHEMY_INSTALL_MSG)
