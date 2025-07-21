"""Constants for the database schemas."""

import contextlib
import os

from alembic.config import Config
import filelock
import sqlalchemy

DB_INIT_LOCK_TIMEOUT_SECONDS = 10

GLOBAL_USER_STATE_DB_NAME = 'state_db'
GLOBAL_USER_STATE_VERSION = '001'
GLOBAL_USER_STATE_LOCK_PATH = '~/.sky/locks/.state_db.lock'

SKYPILOT_CONFIG_DB_NAME = 'skypilot_config_db'
SKYPILOT_CONFIG_VERSION = '001'
SKYPILOT_CONFIG_LOCK_PATH = '~/.sky/locks/.skypilot_config_db.lock'

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
