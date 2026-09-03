"""Refresh the locally managed Vast catalog when credentials are available."""

import csv
import os
from pathlib import Path
import tempfile
import time

import filelock

from sky.catalog import common as catalog_common
from sky.catalog.data_fetchers import fetch_vast

CATALOG_FILENAME = 'vast/vms.csv'
DEFAULT_MAX_AGE_SECONDS = 20 * 60
_CREDENTIAL_PATH = '~/.config/vastai/vast_api_key'
_REQUIRED_COLUMNS = {
    'InstanceType',
    'AcceleratorName',
    'AcceleratorCount',
    'vCPUs',
    'MemoryGiB',
    'GpuInfo',
    'Price',
    'SpotPrice',
    'Region',
}


def has_credentials() -> bool:
    """Return whether the Vast credential file permits a local refresh."""
    return Path(os.path.expanduser(_CREDENTIAL_PATH)).is_file()


def validate_catalog(path: Path) -> None:
    """Validate the CSV columns and ensure at least one usable GPU row."""
    with path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        missing_columns = _REQUIRED_COLUMNS.difference(reader.fieldnames or ())
        if missing_columns:
            missing = ', '.join(sorted(missing_columns))
            raise ValueError(
                f'Vast catalog is missing required columns: {missing}')

        for row in reader:
            try:
                accelerator_count = float(row['AcceleratorCount'])
            except (TypeError, ValueError):
                continue
            if (row.get('AcceleratorName') and accelerator_count > 0 and
                    row.get('GpuInfo')):
                return
    raise ValueError('Vast catalog does not contain usable GPU entries')


def catalog_is_fresh(target: Path) -> bool:
    """Return whether a recent, validated local catalog can be reused."""
    max_age_seconds = int(
        os.environ.get('VAST_CATALOG_MAX_AGE_SECONDS', DEFAULT_MAX_AGE_SECONDS))
    if max_age_seconds <= 0 or not target.is_file():
        return False
    age_seconds = max(0.0, time.time() - target.stat().st_mtime)
    if age_seconds > max_age_seconds:
        return False
    try:
        validate_catalog(target)
    except Exception:  # pylint: disable=broad-except
        return False
    print(f'Vast catalog at {target} is {age_seconds:.0f}s old and valid; '
          'skipping refresh')
    return True


def refresh_catalog(force: bool = False) -> bool:
    """Fetch, validate, and atomically install the current Vast catalog.

    A refresh is intentionally disabled unless the Vast credential file is
    available. If a provider call fails, a previously validated CSV remains in
    place and continues to serve catalog queries.

    Args:
        force: Refresh even when the current catalog is still within its
            configured maximum age.
    """
    if not has_credentials():
        return False

    target = Path(catalog_common.get_catalog_path(CATALOG_FILENAME))
    target.parent.mkdir(parents=True, exist_ok=True)
    with filelock.FileLock(str(target) + '.refresh.lock'):
        if not force and catalog_is_fresh(target):
            return True

        file_descriptor, staged_name = tempfile.mkstemp(prefix='.vast-vms-',
                                                        suffix='.csv',
                                                        dir=target.parent)
        os.close(file_descriptor)
        staged = Path(staged_name)
        try:
            fetch_vast.save_catalog(fetch_vast.fetch_vast_catalog(),
                                    str(staged))
            validate_catalog(staged)
            os.replace(staged, target)
            print(f'Refreshed Vast catalog at {target}')
            return True
        except Exception:  # pylint: disable=broad-except
            if target.is_file():
                try:
                    validate_catalog(target)
                except Exception:  # pylint: disable=broad-except
                    pass
                else:
                    print('Vast catalog refresh failed; using the validated '
                          'existing catalog')
                    return True
            raise RuntimeError(
                'Vast catalog refresh failed and no valid existing catalog '
                'is available') from None
        finally:
            staged.unlink(missing_ok=True)
