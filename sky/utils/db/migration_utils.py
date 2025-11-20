"""Constants for the database schemas."""

import contextlib
import logging
import os

from alembic import command as alembic_command
from alembic.config import Config
from alembic.runtime import migration
import filelock
import sqlalchemy

from sky import sky_logging
from sky.skylet import constants

logger = sky_logging.init_logger(__name__)

DB_INIT_LOCK_TIMEOUT_SECONDS = 10

GLOBAL_USER_STATE_DB_NAME = 'state_db'
GLOBAL_USER_STATE_VERSION = '010'
GLOBAL_USER_STATE_LOCK_PATH = f'~/.sky/locks/.{GLOBAL_USER_STATE_DB_NAME}.lock'

SPOT_JOBS_DB_NAME = 'spot_jobs_db'
SPOT_JOBS_VERSION = '006'
SPOT_JOBS_LOCK_PATH = f'~/.sky/locks/.{SPOT_JOBS_DB_NAME}.lock'

SERVE_DB_NAME = 'serve_db'
SERVE_VERSION = '002'
SERVE_LOCK_PATH = f'~/.sky/locks/.{SERVE_DB_NAME}.lock'

SKYPILOT_CONFIG_DB_NAME = 'sky_config_db'
SKYPILOT_CONFIG_VERSION = '001'
SKYPILOT_CONFIG_LOCK_PATH = f'~/.sky/locks/.{SKYPILOT_CONFIG_DB_NAME}.lock'

KV_CACHE_DB_NAME = 'kv_cache_db'
KV_CACHE_VERSION = '001'
KV_CACHE_LOCK_PATH = f'~/.sky/locks/.{KV_CACHE_DB_NAME}.lock'


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
    # From sky/utils/db/migration_utils.py -> sky/setup_files/alembic.ini
    alembic_ini_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        'setup_files', 'alembic.ini')
    alembic_cfg = Config(alembic_ini_path, ini_section=section)

    # Override the database URL to match SkyPilot's current connection
    # Use render_as_string to get the full URL with password
    url = engine.url.render_as_string(hide_password=False)
    # Replace % with %% to escape the % character in the URL
    # set_section_option uses variable interpolation, which treats % as a
    # special character.
    # any '%' symbol not used for interpolation needs to be escaped.
    url = url.replace('%', '%%')
    alembic_cfg.set_section_option(section, 'sqlalchemy.url', url)

    return alembic_cfg


def needs_upgrade(engine: sqlalchemy.engine.Engine, section: str,
                  target_revision: str):
    """Check if the database needs to be upgraded.

    Args:
        engine: SQLAlchemy engine for the database
        section: Alembic section to upgrade (e.g., 'state_db' or 'spot_jobs_db')
        target_revision: Target revision to upgrade to (e.g., '001')
    """
    current_rev = None

    # get alembic config for the given section
    alembic_config = get_alembic_config(engine, section)
    version_table = alembic_config.get_section_option(
        alembic_config.config_ini_section, 'version_table', 'alembic_version')

    with engine.connect() as connection:
        context = migration.MigrationContext.configure(
            connection, opts={'version_table': version_table})
        current_rev = context.get_current_revision()

    target_rev_num = int(target_revision)
    if current_rev is None:
        if os.environ.get(constants.ENV_VAR_IS_SKYPILOT_SERVER) is not None:
            logger.debug(f'{section} database currently uninitialized, '
                         f'targeting revision {target_rev_num}')
        return True

    # Compare revisions - assuming they are numeric strings like '001', '002'
    current_rev_num = int(current_rev)
    if (current_rev_num < target_rev_num and
            os.environ.get(constants.ENV_VAR_IS_SKYPILOT_SERVER) is not None):
        logger.debug(
            f'{section} database currently at revision {current_rev_num}, '
            f'targeting revision {target_rev_num}')

    return current_rev_num < target_rev_num


def safe_alembic_upgrade(engine: sqlalchemy.engine.Engine, section: str,
                         target_revision: str):
    """Upgrade the database if needed. Uses a file lock to ensure
    that only one process tries to upgrade the database at a time.

    Args:
        engine: SQLAlchemy engine for the database
        section: Alembic section to upgrade (e.g., 'state_db' or 'spot_jobs_db')
        target_revision: Target revision to upgrade to (e.g., '001')
    """
    # set alembic logger to warning level
    alembic_logger = logging.getLogger('alembic')
    alembic_logger.setLevel(logging.WARNING)

    alembic_config = get_alembic_config(engine, section)

    # only acquire lock if db needs upgrade
    if needs_upgrade(engine, section, target_revision):
        with db_lock(section):
            # check again if db needs upgrade in case another
            # process upgraded it while we were waiting for the lock
            if needs_upgrade(engine, section, target_revision):
                alembic_command.upgrade(alembic_config, target_revision)
