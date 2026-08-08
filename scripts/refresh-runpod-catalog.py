#!/usr/bin/env python3
"""Refresh SkyPilot's RunPod catalog without exposing a partial cache."""

from __future__ import annotations

import ast
import csv
import os
from pathlib import Path
import tempfile
import time
from typing import Any

import runpod
from runpod.api import graphql
from sky.catalog import common as catalog_common
from sky.catalog.data_fetchers import fetch_runpod

DEFAULT_MAX_AGE_SECONDS = 1800

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


def validate_catalog(path: Path) -> None:
    """Validate the catalog shape and per-GPU VRAM before installation."""
    validated_gpu_infos: set[str] = set()
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        missing_columns = set(fetch_runpod.USEFUL_COLUMNS) - set(reader.fieldnames or ())
        if missing_columns:
            names = ", ".join(sorted(missing_columns))
            raise ValueError(f"RunPod catalog is missing required columns: {names}")

        gpu_rows = 0
        for row in reader:
            if not row.get("AcceleratorName"):
                continue
            gpu_rows += 1
            serialized = row["GpuInfo"]
            # Identical across a GPU's ~40 region/zone rows; parse each value once.
            if serialized in validated_gpu_infos:
                continue
            gpu_info = ast.literal_eval(serialized)
            gpus = gpu_info.get("Gpus", [])
            if not gpus or any(gpu.get("MemoryInfo", {}).get("SizeInMiB", 0) <= 0 for gpu in gpus):
                raise ValueError("RunPod catalog entries must contain positive GPU memory")
            validated_gpu_infos.add(serialized)

        if gpu_rows == 0:
            raise ValueError("RunPod catalog does not contain GPU entries")


def catalog_is_fresh(target: Path) -> bool:
    """Reuse a recent validated catalog instead of refetching on every start."""
    max_age_seconds = int(os.environ.get("RUNPOD_CATALOG_MAX_AGE_SECONDS", DEFAULT_MAX_AGE_SECONDS))
    if max_age_seconds <= 0 or not target.is_file():
        return False
    age_seconds = max(0.0, time.time() - target.stat().st_mtime)
    if age_seconds > max_age_seconds:
        return False
    try:
        validate_catalog(target)
    except Exception:  # noqa: BLE001 - an invalid cached catalog means refresh, not crash
        return False
    print(f"RunPod catalog at {target} is {age_seconds:.0f}s old and valid; skipping refresh")
    return True


def try_install_gpu_details_cache() -> None:
    """Serve fetch_runpod.get_gpu_details from one batched GraphQL query.

    The upstream fetcher issues one GraphQL request per (GPU, count) pair,
    roughly 285 requests per refresh. For GPUs in DEFAULT_GPU_INFO the
    count-specific response is unused: vCPUs and memory come from the defaults
    and every other consumed field (securePrice, secureSpotPrice, secureCloud,
    memoryInGb, displayName, manufacturer) is count-independent. One batched
    query therefore serves every count of every known GPU. Unknown GPUs keep
    their per-count queries because get_gpu_info reads their count-specific
    lowestPrice data.
    """
    try:
        api_key = os.environ.get("RUNPOD_API_KEY")
        if not api_key:
            raise ValueError("RUNPOD_API_KEY environment variable not set")
        runpod.api_key = api_key
        result = graphql.run_graphql_query(ALL_GPU_DETAILS_QUERY)
        details_by_id = {gpu["id"]: gpu for gpu in result["data"]["gpuTypes"]}
    except Exception as error:  # noqa: BLE001 - the prefetch is an optimization only
        print(f"Batched GPU prefetch failed ({error}); keeping per-count queries")
        return

    original_get_gpu_details = fetch_runpod.get_gpu_details

    def cached_get_gpu_details(gpu_id: str, gpu_count: int = 1) -> dict[str, Any]:
        cached = details_by_id.get(gpu_id)
        if cached is None:
            return original_get_gpu_details(gpu_id, gpu_count)
        if gpu_count != 1 and fetch_runpod.format_gpu_name(cached) not in fetch_runpod.DEFAULT_GPU_INFO:
            return original_get_gpu_details(gpu_id, gpu_count)
        return cached

    fetch_runpod.get_gpu_details = cached_get_gpu_details


def refresh_catalog() -> None:
    """Fetch, validate, and atomically install the current RunPod catalog."""
    target = Path(catalog_common.get_catalog_path("runpod/vms.csv"))
    target.parent.mkdir(parents=True, exist_ok=True)

    if catalog_is_fresh(target):
        return

    file_descriptor, staged_name = tempfile.mkstemp(prefix=".runpod-vms-", suffix=".csv", dir=target.parent)
    os.close(file_descriptor)
    staged = Path(staged_name)
    try:
        try_install_gpu_details_cache()
        instances = fetch_runpod.fetch_runpod_catalog(no_gpu=False, no_cpu=True)
        fetch_runpod.save_catalog(instances, str(staged))
        validate_catalog(staged)
        os.replace(staged, target)
        print(f"Refreshed RunPod catalog at {target}")
    except Exception:  # noqa: BLE001 - startup falls back only to a validated cache
        if target.is_file():
            try:
                validate_catalog(target)
            except Exception:  # noqa: BLE001 - replace the original failure with a safe message
                pass
            else:
                print("RunPod catalog refresh failed; using the validated existing catalog")
                return
        raise RuntimeError("RunPod catalog refresh failed and no valid existing catalog is available") from None
    finally:
        staged.unlink(missing_ok=True)


if __name__ == "__main__":
    refresh_catalog()
