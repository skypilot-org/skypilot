"""Configuration for carbon catalog."""
import hashlib
import os
import time
from typing import Optional

import filelock
import pandas as pd
import requests

from sky import sky_logging
from sky.clouds import cloud_registry
from sky.utils import rich_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

CARBON_HOSTED_CATALOG_DIR_URL = 'https://raw.githubusercontent.com/GreenAlgorithms/green-algorithms-tool/master/data'  # pylint: disable=line-too-long
CARBON_CATALOG_SCHEMA_VERSION = 'v2.2'
CARBON_LOCAL_CATALOG_DIR = os.path.expanduser('~/.sky/catalogs/carbon')

_CARBON_CATALOG_DIR = os.path.join(CARBON_LOCAL_CATALOG_DIR,
                                   CARBON_CATALOG_SCHEMA_VERSION)

CARBON_PULL_FREQUENCY_HOURS = 7


def get_carbon_catalog_path(filename: str) -> str:
    return os.path.join(_CARBON_CATALOG_DIR, filename)


def read_carbon_file(
        filename: str,
        filter_col: str,
        filter_val: str,
        pull_frequency_hours: Optional[int] = None) -> pd.DataFrame:
    """Reads the carbon file from a local CSV file.

    If the file does not exist, download the up-to-date carbon file that matches
    the schema version.
    If `pull_frequency_hours` is not None: pull the latest carbon file with
    possibly updated carbon metrics, if the local carbon file is older than
    `pull_frequency_hours` and no changes to the local carbon file are
    made after the last pull.
    """
    assert filename.endswith('.csv'), 'The carbon file must be a CSV file.'
    assert (pull_frequency_hours is None or
            pull_frequency_hours >= 0), pull_frequency_hours
    catalog_path = get_carbon_catalog_path(filename)
    cloud_dir_name = os.path.dirname(filename)
    if len(cloud_dir_name) > 0:
        cloud = cloud_registry.CLOUD_REGISTRY.from_str(cloud_dir_name)
    else:
        cloud = None

    meta_path = os.path.join(_CARBON_CATALOG_DIR, '.meta', filename)
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)

    # Atomic check, to avoid conflicts with other processes.
    # TODO(mraheja): remove pylint disabling when filelock version updated
    # pylint: disable=abstract-class-instantiated
    with filelock.FileLock(meta_path + '.lock'):

        def _need_update() -> bool:
            if not os.path.exists(catalog_path):
                return True
            if pull_frequency_hours is None:
                return False
            # Check the md5 of the file to see if it has changed.
            with open(catalog_path, 'rb') as f:
                file_md5 = hashlib.md5(f.read()).hexdigest()
            md5_filepath = meta_path + '.md5'
            if os.path.exists(md5_filepath):
                with open(md5_filepath, 'r') as f:
                    last_md5 = f.read()
                if file_md5 != last_md5:
                    # Do not update the file if the user modified it.
                    return False

            last_update = os.path.getmtime(catalog_path)
            return last_update + pull_frequency_hours * 3600 < time.time()

        if _need_update():
            # TODO: Cleanup hack below for better impl.
            source_filename = filename
            if len(cloud_dir_name) > 0 and str.startswith(
                    filename, cloud_dir_name + '/'):
                source_filename = filename[len(cloud_dir_name) + 1:]
            url = f'{CARBON_HOSTED_CATALOG_DIR_URL}/{CARBON_CATALOG_SCHEMA_VERSION}/{source_filename}'  # pylint: disable=line-too-long
            update_frequency_str = ''
            if pull_frequency_hours is not None:
                update_frequency_str = f' (every {pull_frequency_hours} hours)'
            if cloud is None:
                msg_prefix = str('Updating')
            else:
                msg_prefix = str('Updating ' + str(cloud))
            with rich_utils.safe_status((f'{msg_prefix} carbon file: '
                                         f'{filename}'
                                         f'{update_frequency_str}')):
                try:
                    r = requests.get(url)
                    r.raise_for_status()
                except requests.exceptions.RequestException as e:
                    if cloud is None:
                        err_msg_prefix = str('Failed to fetch')
                    else:
                        err_msg_prefix = str('Failed to fetch ' +
                                             str(cloud)) + ' '

                    error_str = (f'{err_msg_prefix} carbon file: '
                                 f'{filename}. ')
                    if os.path.exists(catalog_path):
                        logger.warning(f'{error_str}Using cached carbon files.')
                        # Update carbon file modification time.
                        os.utime(catalog_path, None)  # Sets to current time
                    else:
                        logger.error(
                            f'{error_str}Please check your internet connection.'
                        )
                        with ux_utils.print_exception_no_traceback():
                            raise e
                else:
                    # Download successful, save the carbon file to a local file.
                    os.makedirs(os.path.dirname(catalog_path), exist_ok=True)
                    with open(catalog_path, 'w') as f:
                        f.write(r.text)
                    with open(meta_path + '.md5', 'w') as f:
                        f.write(hashlib.md5(r.text.encode()).hexdigest())
    try:
        df = pd.read_csv(catalog_path, sep=',', skiprows=1)
        # This is used for source that has multiple cloud metrics in one file.
        if len(filter_col) > 0:
            df = df[(df[filter_col] == filter_val)]
    except Exception as e:  # pylint: disable=broad-except
        # As users can manually modify the carbon file, read_csv can fail.
        logger.error(f'Failed to read {catalog_path}. '
                     'To fix: delete the csv file and try again.')
        with ux_utils.print_exception_no_traceback():
            raise e
    return df
