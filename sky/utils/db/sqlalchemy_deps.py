"""Optional SQLAlchemy install helpers for client vs server installs.

SkyPilot's client/CLI package no longer depends on SQLAlchemy. The API server
and managed-jobs controller need the DB stack via ``skypilot[server]`` (or any
cloud extra, which already includes ``server_dependencies``).

Import this module from DB-backed modules so missing or too-old SQLAlchemy
raises a clear install hint instead of a bare ``ModuleNotFoundError`` or an
obscure 1.x API failure.
"""
import importlib.metadata

from packaging import version as version_lib

from sky.adaptors import common as adaptors_common

# Matches sky/setup_files/dependencies.py ``server_dependencies``.
_MIN_SQLALCHEMY_VERSION = version_lib.Version('2.0.16')

SQLALCHEMY_INSTALL_MSG = (
    'SQLAlchemy >= 2.0.16 is required for SkyPilot API server / controller '
    'database access. Install server dependencies with: '
    'pip install "skypilot[server]" '
    '(or a cloud extra such as "skypilot[kubernetes]").')


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
    """Raise ImportError with install hint when SQLAlchemy is missing/too old."""
    if not sqlalchemy_available():
        raise ImportError(SQLALCHEMY_INSTALL_MSG)
