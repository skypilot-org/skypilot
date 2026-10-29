"""Optional SQLAlchemy install helpers for client vs server installs.

SkyPilot's client/CLI package no longer depends on SQLAlchemy. The API server
and managed-jobs controller need the DB stack via ``skypilot[server]`` (or any
cloud extra, which already includes ``server_dependencies``).

Import this module from DB-backed modules so missing SQLAlchemy raises a clear
install hint instead of a bare ``ModuleNotFoundError``.
"""
from sky.adaptors import common as adaptors_common

SQLALCHEMY_INSTALL_MSG = (
    'SQLAlchemy is required for SkyPilot API server / controller database '
    'access. Install server dependencies with: pip install "skypilot[server]" '
    '(or a cloud extra such as "skypilot[kubernetes]").')


def sqlalchemy_available() -> bool:
    """Return True if SQLAlchemy can be imported without loading it."""
    return adaptors_common.can_import_modules(['sqlalchemy'])


def require_sqlalchemy() -> None:
    """Raise ImportError with install hint when SQLAlchemy is missing."""
    if not sqlalchemy_available():
        raise ImportError(SQLALCHEMY_INSTALL_MSG)
