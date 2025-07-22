"""Constants for the database schemas."""

import contextlib
import logging
import os

from alembic import command as alembic_command
from alembic.config import Config
from alembic.runtime import migration
import filelock
import sqlalchemy

DB_INIT_LOCK_TIMEOUT_SECONDS = 10

GLOBAL_USER_STATE_DB_NAME = 'state_db'
GLOBAL_USER_STATE_VERSION = '001'
GLOBAL_USER_STATE_LOCK_PATH = '~/.sky/locks/.state_db.lock'

SPOT_JOBS_DB_NAME = 'spot_jobs_db'
SPOT_JOBS_VERSION = '001'
SPOT_JOBS_LOCK_PATH = '~/.sky/locks/.spot_jobs_db.lock'


@contextlib.contextmanager
def db_lock(db_name: str):
    lock_path = os.path.expanduser(f'~/.sky/locks/.{db_name}.lock')
    try:
        with filelock.FileLock(lock_path, timeout=DB_INIT_LOCK_TIMEOUT_SECONDS):
            yield
    except filelock.Timeout as e:
        raise RuntimeError(f'Failed to initialize database due to a timeout '
                           f'when trying to acquire the lock at '
                           f'{lock_path}. '
                           'Please try again or manually remove the lock '
                           f'file if you believe it is stale.') from e


def get_alembic_config(engine: sqlalchemy.engine.Engine, section: str):
    """Get Alembic configuration for the given section"""
    # Use the alembic.ini file from setup_files (included in wheel)
    # From sky/utils/db/migration_utils.py -> sky/setup_files/alembic.ini
    alembic_ini_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        'setup_files', 'alembic.ini')
    alembic_cfg = Config(alembic_ini_path, ini_section=section)

    # Override the database URL to match SkyPilot's current connection
    # Use render_as_string to get the full URL with password
    url = engine.url.render_as_string(hide_password=False)
    alembic_cfg.set_section_option(section, 'sqlalchemy.url', url)

    return alembic_cfg


def safe_alembic_upgrade(engine: sqlalchemy.engine.Engine,
                         alembic_config: Config, target_revision: str):
    """Only upgrade if current version is older than target.

    This handles the case where a database was created with a newer version of
    the code and we're now running older code. Since our migrations are purely
    additive, it's safe to run a newer database with older code.

    Args:
        engine: SQLAlchemy engine for the database
        alembic_config: Alembic configuration object
        target_revision: Target revision to upgrade to (e.g., '001')
    """
    # set alembic logger to warning level
    alembic_logger = logging.getLogger('alembic')
    alembic_logger.setLevel(logging.WARNING)

    current_rev = None

    # Get the current revision from the database
    version_table = alembic_config.get_section_option(
        alembic_config.config_ini_section, 'version_table', 'alembic_version')

    with engine.connect() as connection:
        context = migration.MigrationContext.configure(
            connection, opts={'version_table': version_table})
        current_rev = context.get_current_revision()

    if current_rev is None:
        alembic_command.upgrade(alembic_config, target_revision)
        return

    # Compare revisions - assuming they are numeric strings like '001', '002'
    current_rev_num = int(current_rev)
    target_rev_num = int(target_revision)

    # only upgrade if current revision is older than target revision
    if current_rev_num < target_rev_num:
        alembic_command.upgrade(alembic_config, target_revision)
