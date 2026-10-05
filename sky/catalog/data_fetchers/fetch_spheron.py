"""Fetch Spheron GPU offers and emit a SkyPilot catalog CSV.

Modelled on ``sky/catalog/data_fetchers/fetch_shadeform.py`` (SkyPilot 0.13.0),
but Spheron's payload differs in ways that are easy to get wrong. The three
that would silently corrupt the catalog:

1. **Price units.** Shadeform reports ``hourly_price`` in CENTS and its fetcher
   divides by 100. Spheron reports DOLLARS already (a B200 x8 offer reads
   ``57.6071`` and the API docs state an "8x H100 offer priced at 21.52 means
   21.52 USD per hour total"). Dividing here would under-price every row 100x
   and make the planner always pick Spheron.

2. **``available`` carries no signal.** All 312 offers in the 2026-09-10
   snapshot report ``available: true`` -- including 106 flagged
   ``maintenance: true``. Filtering on ``available`` alone is therefore a no-op.
   We filter on ``maintenance`` instead and treat the catalog as advisory, the
   same lesson as Shadeform's unbuyable-SKU problem.

3. **Region strings are not uniform.** Some are plain (``kansascity-usa-1``),
   some carry an opaque routing prefix (``166e655a:culpeper-usa-1``). The
   prefixed form is what ``POST /api/deployments`` expects back, so the CSV
   stores the string VERBATIM and geo lookup strips the prefix separately.

Deliberately excluded from the GPU catalog, each of which would otherwise fail
late and expensively:

* ``spheron-am`` -- AMD Instinct / ROCm. Our runtime images are CUDA-only, so
  these boot fine and then fail at CUDA import, ~90 minutes in.
* ``gpuCount == 0`` -- CPU Node offers. Not a GPU catalog row.
* ``maintenance: true`` -- provider has flagged the capacity.

This module performs NO silent fallback: an unparseable offer raises rather
than being skipped, so a schema change surfaces at fetch time instead of
becoming a missing row nobody notices.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, Iterator, List, Optional

API_BASE = "https://app.spheron.ai"
OFFERS_PATH = "/api/gpu-offers"

# The API returns 403 to urllib's default user-agent while accepting curl, so
# an explicit UA is mandatory, not cosmetic (observed 2026-09-10).
USER_AGENT = "urun-skypilot-spheron/0.1 (+https://urun.sh)"

PAGE_LIMIT = 50

# Providers whose silicon our CUDA runtime images cannot run.
EXCLUDED_PROVIDERS = frozenset({"spheron-am"})

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


class SpheronCatalogError(RuntimeError):
    """Raised when the upstream payload cannot be interpreted.

    Loud by design: a silently skipped offer is a row that quietly vanishes
    from the planner's view.
    """


def _request(url: str, api_key: str) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        # Never echo the key. 429 is the documented rate limit: 100 req/15 min.
        raise SpheronCatalogError(
            f"GET {url.split('?')[0]} failed: HTTP {exc.code}"
        ) from exc


def fetch_pages(api_key: str, limit: int = PAGE_LIMIT) -> List[Dict[str, Any]]:
    """Page through /api/gpu-offers until totalPages is exhausted."""
    pages: List[Dict[str, Any]] = []
    page = 1
    while True:
        url = f"{API_BASE}{OFFERS_PATH}?page={page}&limit={limit}"
        payload = _request(url, api_key)
        pages.append(payload)
        total_pages = int(payload.get("totalPages") or 1)
        if page >= total_pages:
            return pages
        page += 1


def normalise_accelerator(gpu_type: str) -> str:
    """Spheron GPU id -> SkyPilot accelerator name.

    Mirrors the Shadeform fetcher's suffix handling so a shape reads the same
    across clouds: a trailing ``G`` means gigabytes and becomes ``GB``
    (``A100_80G`` -> ``A100-80GB``), and an embedded ``Gx`` becomes ``GBx``.
    """
    name = gpu_type.strip().replace("_", "-")
    if name.endswith("G"):
        name += "B"
    if "Gx" in name:
        name = name.replace("Gx", "GBx")
    return name


def strip_region_prefix(region: str) -> str:
    """``166e655a:culpeper-usa-1`` -> ``culpeper-usa-1``.

    Only for geo lookup. The CSV keeps the verbatim string, because that is
    what POST /api/deployments requires back.
    """
    return region.split(":", 1)[1] if ":" in region else region


def _gpu_info(accelerator: str, count: int, vram_gb: int) -> str:
    """SkyPilot's GpuInfo blob. Single quotes, matching the other fetchers."""
    info = {
        "Gpus": [
            {
                "Name": accelerator,
                "Manufacturer": "NVIDIA",
                "Count": float(count),
                "MemoryInfo": {"SizeInMiB": int(vram_gb) * 1024},
            }
        ],
        "TotalGpuMemoryInMiB": int(vram_gb) * 1024 * int(count),
    }
    return json.dumps(info).replace('"', "'")


def _require(offer: Dict[str, Any], key: str, offer_id: str) -> Any:
    if key not in offer or offer[key] is None:
        raise SpheronCatalogError(
            f"offer {offer_id!r} is missing required field {key!r}"
        )
    return offer[key]


def iter_rows(pages: Iterable[Dict[str, Any]]) -> Iterator[List[Any]]:
    """Yield one CSV row per (offer, region) pair we are willing to rent."""
    for payload in pages:
        for group in payload.get("data", []):
            gpu_type = group.get("gpuType")
            if not gpu_type:
                raise SpheronCatalogError(f"gpu group without gpuType: {sorted(group)}")
            accelerator = normalise_accelerator(gpu_type)

            for offer in group.get("offers", []):
                # Identity first: an offer without BOTH halves cannot be
                # deployed, and a row like 'None_x' or 'sesterce_<no offerId>'
                # would sit in the catalog looking selectable and then fail at
                # provision time. (CodeRabbit on #338.)
                offer_id = offer.get("offerId")
                provider = offer.get("provider")
                if not offer_id or not provider:
                    raise SpheronCatalogError(
                        "offer is missing deployment identity "
                        f"(provider={provider!r}, offerId={offer_id!r}); "
                        "it could never be provisioned"
                    )

                if provider in EXCLUDED_PROVIDERS:
                    continue
                if offer.get("maintenance"):
                    continue
                gpu_count = int(_require(offer, "gpuCount", offer_id))
                if gpu_count == 0:
                    continue  # CPU Node, not a GPU catalog row.

                vram = int(_require(offer, "gpu_memory", offer_id))
                vcpus = float(_require(offer, "vcpus", offer_id))
                memory = float(_require(offer, "memory", offer_id))
                # DOLLARS already -- see module docstring. Do not divide.
                price = float(_require(offer, "price", offer_id))
                spot_price = offer.get("spot_price") or ""

                # SkyPilot reads `Price` as the ON-DEMAND rate and `SpotPrice`
                # as the preemptible one. A SPOT-only offer that fills both
                # columns is selectable by an ON-DEMAND request, which would
                # then be provisioned against `instanceType: SPOT` and could be
                # reclaimed (`terminated-provider`) under a claim that asked
                # for dedicated capacity. So a SPOT offer publishes ONLY a spot
                # price. (Greptile on #338.)
                if str(offer.get("instanceType") or "").upper() == "SPOT":
                    spot_price = spot_price or price
                    price = ""

                clusters = offer.get("clusters") or []
                if not clusters:
                    raise SpheronCatalogError(
                        f"offer {offer_id!r} lists no clusters/regions"
                    )

                # InstanceType must round-trip to a deployable offer, and the
                # deploy call needs provider + offerId together.
                instance_type = f"{provider}_{offer_id}"
                gpu_info = _gpu_info(accelerator, gpu_count, vram)

                for region in clusters:
                    yield [
                        instance_type,
                        accelerator,
                        float(gpu_count),
                        vcpus,
                        memory,
                        price,
                        region,
                        gpu_info,
                        spot_price,
                    ]


def write_csv(rows: Iterable[List[Any]], out_path: str) -> int:
    written = 0
    with open(out_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=",", quotechar='"')
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row)
            written += 1
    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key-env", default="SPHERON_API_KEY")
    parser.add_argument("--output", default="spheron/vms.csv")
    parser.add_argument(
        "--from-fixture", help="read a recorded payload instead of the network"
    )
    args = parser.parse_args(argv)

    if args.from_fixture:
        with open(args.from_fixture, encoding="utf-8") as handle:
            pages = json.load(handle)["pages"]
    else:
        import os

        api_key = os.environ.get(args.api_key_env, "").strip()
        if not api_key:
            # Fail hard: an empty key would otherwise yield an empty catalog,
            # which reads as "Spheron has no capacity" rather than "unconfigured".
            raise SpheronCatalogError(
                f"{args.api_key_env} is not set; refusing to emit an empty catalog"
            )
        pages = fetch_pages(api_key)

    count = write_csv(iter_rows(pages), args.output)
    print(f"wrote {count} rows to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
