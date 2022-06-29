"""Global environment options for sky."""
import os


def _check_bool_env_var(env_var_name):
    """Check if an environment variable is set to True."""
    return os.getenv(env_var_name, 'False').lower() in ('true', '1')


IS_DEVELOPPING = _check_bool_env_var('SKY_DEV')

DISABLE_LOGGING = _check_bool_env_var('SKY_DISABLE_STATS_COLLECTION')

MINIMIZE_LOGGING = _check_bool_env_var('SKY_MINIMIZE_LOGGING')
