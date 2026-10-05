"""Fetch Latitude.sh GPU plans and emit a SkyPilot catalog CSV.

One row per (plan, site) pair the lane can actually rent, from
``GET /plans?filter[gpu]=true``:

* **In-stock only.** A plan's ``regions[].locations.in_stock`` is the
  deployable-right-now set; ``available`` is the broader deployable set and
  the regions list alone is NOT availability. The catalog shows only what
  the lane can rent (the same quote-vs-launch agreement the Vast fetcher
  enforces): a row the launch must refuse lets the QUOTE admit candidates
  the LAUNCH cannot serve.
* **Region is the site slug** (``ASH``, ``CHI``, ...): the controller's
  residency filter exact-matches ``Region`` against budget
  ``allowedRegions`` tokens, so the catalog must emit the same token the
  deploy API takes as ``site``.
* **Price is the per-region ``pricing.USD.hour`` for the WHOLE box** (a
  4-GPU plan's hourly price is for the entire server, not per GPU).
* Plans without ``ssh`` in ``features`` are skipped: the lane logs in with
  an attached SSH key, and a plan that cannot take one is unprovisionable.
* Plans whose in-stock region carries no USD hourly price are skipped:
  an unpriceable row would quote as free (fail closed, not silent).

Needs ``LATITUDESH_API_KEY``; without it the fetcher REFUSES to run rather
than emit an empty catalog that would read as "no capacity".
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, Iterator, List, Optional

from sky.adaptors import latitude as latitude_api


CSV_COLUMNS = [
    "InstanceType",
    "AcceleratorName",
    "AcceleratorCount",
    "vCPUs",
    "MemoryGiB",
    "Price",
    "Region",
    "GpuInfo",
    "SpotPrice",
]

# Latitude lists GPU model strings like "NVIDIA H100", "NVIDIA RTX PRO 6000".
# After stripping the vendor and whitespace, map the Blackwell RTX 6000
# variants to the ONE accelerator token the platform's identities use for
# the family (RTXPRO6000 — the same family the Vast and Spheron lanes map
# to; see fetch_vast's map and _PROVIDER_GPU_IDENTITIES in
# skypilot-controller). Unmapped names pass through unchanged: an
# unrecognized GPU is a real accelerator nobody requests through our
# identities, and passing it through keeps it visible in the catalog for
# audit instead of silently hiding the plan.
_ACCELERATOR_MAP = {
    "RTXPRO6000S": "RTXPRO6000",
    "RTXPRO6000D": "RTXPRO6000",
    "RTXPRO6000WS": "RTXPRO6000",
    "RTXPRO6000Max-Q": "RTXPRO6000",
    "RTXPRO6000MaxQ": "RTXPRO6000",
    # China-market Blackwell SKU of the RTX PRO 6000 class.
    "RTX6000D": "RTXPRO6000",
}


class LatitudeCatalogError(RuntimeError):
    """Raised when the fetcher cannot run or the payload is unusable."""


def normalise_accelerator(gpu_type: Optional[str]) -> str:
    name = str(gpu_type or "").strip()
    if name.lower().startswith("nvidia"):
        name = name[len("nvidia"):].strip()
    compact = re.sub(r"\s", "", name)
    return _ACCELERATOR_MAP.get(compact, compact)


def fetch_plans(api_key: str) -> List[Dict[str, Any]]:
    client = latitude_api.LatitudeClient(api_key)
    return client.list_plans()



def _gpu_info_str(accelerator: str, count: int, vram_gb_per_gpu: Optional[float]) -> str:
    # JSON with double quotes swapped for single quotes, the convention the
    # other fetchers use for the GpuInfo column.
    return json.dumps(_gpu_info_dict(accelerator, count, vram_gb_per_gpu)).replace('"', "'")


def _gpu_info_dict(accelerator: str, count: int, vram_gb_per_gpu: Optional[float]) -> Dict[str, Any]:
    size_mib = int(vram_gb_per_gpu * 1024) if vram_gb_per_gpu else 0
    return {
        "Gpus": [
            {
                "Name": accelerator,
                "Count": count,
                "MemoryInfo": {"SizeInMiB": size_mib},
            }
        ],
        "TotalGpuMemoryInMiB": size_mib * count,
    }


def _in_stock_sites(region_entry: Dict[str, Any]) -> List[str]:
    locations = region_entry.get("locations") or {}
    sites = locations.get("in_stock") or []
    return [str(site) for site in sites if site]


def iter_rows(plans: Iterable[Dict[str, Any]]) -> Iterator[List[Any]]:
    """Yield one CSV row per (plan, in-stock site) pair we are willing to rent."""
    for plan in plans:
        attrs = plan.get("attributes") or {}
        slug = attrs.get("slug")
        specs = attrs.get("specs") or {}
        gpu = specs.get("gpu") or {}
        gpu_count = gpu.get("count") or 0
        if not slug or not isinstance(gpu_count, (int, float)) or gpu_count < 1:
            # filter[gpu]=true should have excluded these; re-check rather
            # than trust, and skip rather than emit a bogus row.
            continue
        features = attrs.get("features") or []
        if "ssh" not in features:
            continue  # the lane logs in with an attached SSH key
        accelerator = normalise_accelerator(gpu.get("type"))
        if not accelerator:
            continue  # GPU-less or nameless plans are not this lane's catalog
        cpu = specs.get("cpu") or {}
        cores = cpu.get("cores") or 0
        cpu_count = cpu.get("count") or 1
        vcpus = float(cores) * float(cpu_count) if cores else 0.0
        memory = specs.get("memory") or {}
        mem_gib = memory.get("total") or 0
        vram_per_gpu = gpu.get("vram_per_gpu")

        for region_entry in attrs.get("regions") or []:
            if not isinstance(region_entry, dict):
                continue
            pricing = (region_entry.get("pricing") or {}).get("USD") or {}
            hour = pricing.get("hour")
            if hour is None:
                # Unpriceable site: skip rather than quote as free.
                continue
            for site in _in_stock_sites(region_entry):
                yield [
                    str(slug),
                    accelerator,
                    int(gpu_count),
                    vcpus,
                    float(mem_gib),
                    float(hour),
                    site,
                    _gpu_info_str(accelerator, int(gpu_count), vram_per_gpu),
                    "",  # no spot tier on Latitude
                ]


def write_csv(rows: Iterable[List[Any]], out_path: str) -> int:
    written = 0
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row)
            written += 1
    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key-env", default=latitude_api.API_KEY_ENV)
    parser.add_argument("--output", default="latitude/vms.csv")
    parser.add_argument(
        "--from-fixture", help="read a recorded payload instead of the network"
    )
    args = parser.parse_args(argv)

    if args.from_fixture:
        with open(args.from_fixture, encoding="utf-8") as handle:
            plans = json.load(handle)["plans"]
    else:
        api_key = os.environ.get(args.api_key_env, "").strip()
        if not api_key:
            # Fail hard: an empty key would otherwise yield an empty catalog,
            # which reads as "Latitude has no capacity" rather than
            # "unconfigured".
            raise LatitudeCatalogError(
                f"{args.api_key_env} is not set; refusing to emit an empty catalog"
            )
        plans = fetch_plans(api_key)

    count = write_csv(iter_rows(plans), args.output)
    print(f"wrote {count} rows to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
