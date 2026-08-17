"""RunPod catalog refresh support used by the API daemon and CLI tool."""

from __future__ import annotations

import ast
import csv
import os
from pathlib import Path
import tempfile
import time
from typing import Any

import filelock

from sky.catalog import common as catalog_common

runpod: Any = None
graphql: Any = None
fetch_runpod: Any = None

DEFAULT_MAX_AGE_SECONDS = 20 * 60

# Same field set as fetch_runpod.get_gpu_details, so cached entries are
# drop-in replacements for its per-count query results.
ALL_GPU_DETAILS_QUERY = """
query GpuTypes {
  gpuTypes {
    maxGpuCount
    id
    displayName
    manufacturer
    memoryInGb
    cudaCores
    secureCloud
    communityCloud
    securePrice
    communityPrice
    oneMonthPrice
    threeMonthPrice
    oneWeekPrice
    communitySpotPrice
    secureSpotPrice
    lowestPrice(input: {gpuCount: 1}) {
      minimumBidPrice
      uninterruptablePrice
      minVcpu
      minMemory
      stockStatus
      compliance
      maxUnreservedGpuCount
      availableGpuCounts
    }
  }
}
"""


def _load_dependencies():
    """Load optional RunPod dependencies only when a refresh is requested."""
    # pylint: disable=import-outside-toplevel
    global runpod, graphql, fetch_runpod
    if runpod is None or graphql is None:
        import runpod as runpod_module
        from runpod.api import graphql as graphql_module
        runpod = runpod_module
        graphql = graphql_module
    if fetch_runpod is None:
        from sky.catalog.data_fetchers import fetch_runpod as fetcher_module
        fetch_runpod = fetcher_module
    return runpod, graphql, fetch_runpod


def has_credentials() -> bool:
    """Return whether the API server can use RunPod credentials."""
    if os.environ.get('RUNPOD_API_KEY'):
        return True
    return Path(os.path.expanduser('~/.runpod/config.toml')).is_file()


def _configure_credentials() -> None:
    """Load the configured RunPod key into the SDK, if needed."""
    # pylint: disable=import-outside-toplevel, protected-access
    runpod_module, _, _ = _load_dependencies()
    if os.environ.get('RUNPOD_API_KEY'):
        runpod_module.api_key = os.environ['RUNPOD_API_KEY']
        return
    from sky.provision.runpod import utils as runpod_utils
    runpod_utils._ensure_api_key_configured()
    if not getattr(runpod_module, 'api_key', None):
        raise ValueError('RunPod API key is not configured')


def validate_catalog(path: Path) -> None:
    """Validate the catalog shape and per-GPU VRAM before installation."""
    _, _, fetcher = _load_dependencies()
    validated_gpu_infos: set[str] = set()
    with path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        missing_columns = set(fetcher.USEFUL_COLUMNS) - set(reader.fieldnames or
                                                            ())
        if missing_columns:
            names = ', '.join(sorted(missing_columns))
            raise ValueError(
                f'RunPod catalog is missing required columns: {names}')

        gpu_rows = 0
        for row in reader:
            if not row.get('AcceleratorName'):
                continue
            gpu_rows += 1
            serialized = row['GpuInfo']
            if serialized in validated_gpu_infos:
                continue
            gpu_info = ast.literal_eval(serialized)
            gpus = gpu_info.get('Gpus', [])
            if not gpus or any(
                    gpu.get('MemoryInfo', {}).get('SizeInMiB', 0) <= 0
                    for gpu in gpus):
                raise ValueError(
                    'RunPod catalog entries must contain positive GPU memory')
            validated_gpu_infos.add(serialized)

        if gpu_rows == 0:
            raise ValueError('RunPod catalog does not contain GPU entries')


def catalog_is_fresh(target: Path) -> bool:
    """Reuse a recent validated catalog instead of refetching."""
    max_age_seconds = int(
        os.environ.get('RUNPOD_CATALOG_MAX_AGE_SECONDS',
                       DEFAULT_MAX_AGE_SECONDS))
    if max_age_seconds <= 0 or not target.is_file():
        return False
    age_seconds = max(0.0, time.time() - target.stat().st_mtime)
    if age_seconds > max_age_seconds:
        return False
    try:
        validate_catalog(target)
    except Exception:  # pylint: disable=broad-except
        return False
    print(f'RunPod catalog at {target} is {age_seconds:.0f}s old and valid; '
          'skipping refresh')
    return True


def try_install_gpu_details_cache() -> None:
    """Serve GPU details from one batched GraphQL query when possible."""
    _, graphql_module, fetcher = _load_dependencies()
    try:
        _configure_credentials()
        result = graphql_module.run_graphql_query(ALL_GPU_DETAILS_QUERY)
        details_by_id = {gpu['id']: gpu for gpu in result['data']['gpuTypes']}
    except Exception as error:  # pylint: disable=broad-except
        print(
            f'Batched GPU prefetch failed ({error}); keeping per-count queries')
        return

    original_get_gpu_details = fetcher.get_gpu_details

    def cached_get_gpu_details(gpu_id: str,
                               gpu_count: int = 1) -> dict[str, Any]:
        cached = details_by_id.get(gpu_id)
        if cached is None:
            return original_get_gpu_details(gpu_id, gpu_count)
        if gpu_count != 1 and fetcher.format_gpu_name(
                cached) not in fetcher.DEFAULT_GPU_INFO:
            return original_get_gpu_details(gpu_id, gpu_count)
        return cached

    fetcher.get_gpu_details = cached_get_gpu_details


def refresh_catalog() -> None:
    """Fetch, validate, and atomically install the current RunPod catalog."""
    _, _, fetcher = _load_dependencies()
    target = Path(catalog_common.get_catalog_path('runpod/vms.csv'))
    target.parent.mkdir(parents=True, exist_ok=True)

    # Multiple API workers can schedule the daemon during startup.  Serialize
    # the network fetch and re-check freshness after taking the lock so only
    # one worker refreshes the shared catalog.
    with filelock.FileLock(str(target) + '.refresh.lock'):
        if catalog_is_fresh(target):
            return

        file_descriptor, staged_name = tempfile.mkstemp(prefix='.runpod-vms-',
                                                        suffix='.csv',
                                                        dir=target.parent)
        os.close(file_descriptor)
        staged = Path(staged_name)
        try:
            try_install_gpu_details_cache()
            _configure_credentials()
            instances = fetcher.fetch_runpod_catalog(no_gpu=False, no_cpu=True)
            fetcher.save_catalog(instances, str(staged))
            validate_catalog(staged)
            os.replace(staged, target)
            print(f'Refreshed RunPod catalog at {target}')
        except Exception:  # pylint: disable=broad-except
            if target.is_file():
                try:
                    validate_catalog(target)
                except Exception:  # pylint: disable=broad-except
                    pass
                else:
                    print('RunPod catalog refresh failed; using the validated '
                          'existing catalog')
                    return
            raise RuntimeError(
                'RunPod catalog refresh failed and no valid existing catalog '
                'is available') from None
        finally:
            staged.unlink(missing_ok=True)
