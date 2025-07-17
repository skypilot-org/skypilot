"""Constants for the database schemas."""

import contextlib

import filelock

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
    lock_path = f'~/.sky/locks/.{db_name}.lock'
    try:
        with filelock.FileLock(lock_path, timeout=DB_INIT_LOCK_TIMEOUT_SECONDS):
            yield
    except filelock.Timeout as e:
        raise RuntimeError(f'Failed to initialize database due to a timeout '
                           f'when trying to acquire the lock at '
                           f'{lock_path}. '
                           'Please try again or manually remove the lock '
                           f'file if you believe it is stale.') from e
