"""Fetch QuantaCloud GPU offers and emit a SkyPilot catalog CSV.

One row per in-stock (GPU slug, count, region) triple the lane can actually
rent, from the PUBLIC ``GET /offers`` endpoint (no auth needed):

* **Unfiltered fetch, local filter.** The offers endpoint returns ONLY
  in-stock offers, and a server-side filter that matches nothing (bogus
  slug, empty continent) returns the same ``200 {offers: [], totalElements:
  0}`` as honest no-stock — probed live 2026-10-06. This fetcher therefore
  applies NO server-side filters: an empty result can only mean "sold
  out", and it is cross-checked against ``GET /gpu-families`` before being
  written (see ``verify_zero_stock``).
* **MIG variants are excluded.** ``rtx-pro-6000-blackwell-mig-48gb`` is a
  PARTITION of the 96GB card; a MIG slice must never price as the whole
  GPU under the ``RTXPRO6000`` token (the claim would believe it rented
  96GB/GPU).
* **Region is the provider's region token** (``us-east-1``,
  ``us-midwest-1``...): the controller's residency filter exact-matches
  ``Region`` against budget ``allowedRegions`` tokens, and the provisioner
  re-resolves offers by the same token — the catalog must emit exactly the
  token the deploy API filters by.
* **Price is the whole-box ``priceHourly``** (a 4-GPU offer's hourly price
  is for the entire box, not per GPU).
* **InstanceType is ``<gpu-slug>:<count>``** — offer UUIDs are ephemeral
  (an out-of-stock offer 404s), so the stable slug+count pair is the
  catalog identity and the provisioner re-resolves the concrete offer at
  launch. Multiple live offers for the same (slug, count, region) collapse
  to the CHEAPEST row (the launch re-resolution prices from the live list
  anyway; the catalog's job is a stable, rentable quote).
* Offers without a usable price are skipped: an unpriceable row would
  quote as free (fail closed, not silent).

SSH is implicit on every API deployment (the stock image injects the
account's keys), so — unlike the Latitude fetcher — there is no per-offer
``ssh`` feature to check.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from sky.adaptors import quantacloud as quantacloud_api

CSV_COLUMNS = [
    'InstanceType',
    'AcceleratorName',
    'AcceleratorCount',
    'vCPUs',
    'MemoryGiB',
    'Price',
    'Region',
    'GpuInfo',
    'SpotPrice',
]

# GPU slug -> the ONE accelerator token the platform's identities use for
# that family (see fetch_latitude's map and _PROVIDER_GPU_IDENTITIES in
# skypilot-controller). Keyed by SLUG (machine-stable, unlike the display
# name "NVIDIA RTX PRO 6000 Blackwell"); every variant of a family maps to
# the same token. MIG slugs never reach this map (excluded upstream).
# Unmapped slugs pass through compacted (uppercased, dashes removed): an
# unrecognized GPU is a real accelerator nobody requests through our
# identities, and passing it through keeps it visible in the catalog for
# audit instead of silently hiding the offer.
_ACCELERATOR_MAP = {
    # Every whole-card RTX PRO 6000 variant -> the family the rtx6000
    # shape's identities carry (same token as the Vast/Spheron/Latitude
    # lanes).
    'rtx-pro-6000-blackwell': 'RTXPRO6000',
    'rtx-6000-ada': 'RTX6000ADA',
    'rtx-a6000': 'RTXA6000',
    'h200-sxm': 'H200',
    'h200-nvl': 'H200',
    'gh200': 'H200',
    'h100-sxm-80gb': 'H100',
    'h100-pcie-80gb': 'H100',
    'h100-nvl': 'H100',
    'a100-sxm-80gb': 'A100',
    'a100-pcie-80gb': 'A100',
    'a100-sxm-40gb': 'A100',
    'a100-pcie-40gb': 'A100',
    'l40s': 'L40S',
    'l40': 'L40',
}


class QuantacloudCatalogError(RuntimeError):
    """Raised when the fetcher cannot run or the payload is unusable."""


def normalise_accelerator(gpu_slug: Optional[str]) -> str:
    slug = str(gpu_slug or '').strip()
    if not slug:
        return ''
    return _ACCELERATOR_MAP.get(slug, slug.replace('-', '').upper())


def instance_type_token(gpu_slug: str, gpu_count: int) -> str:
    """The stable catalog identity: ``<gpu-slug>:<count>``.

    Offer UUIDs are ephemeral; slug+count is what the controller pins and
    what the provisioner re-resolves against the live list at launch.
    """
    return f'{gpu_slug}:{gpu_count}'


def parse_instance_type_token(instance_type: str) -> Tuple[str, int]:
    """The inverse of ``instance_type_token``; raises on garbage.

    The provisioner's launch-time offer re-resolution starts here: the
    catalog handed it the token, and it needs the (slug, count) pair to
    find the concrete live offer. GPU slugs are kebab-case without colons,
    so splitting on the LAST colon is unambiguous.
    """
    slug, sep, count = str(instance_type or '').rpartition(':')
    if not sep or not slug or not count.isdigit() or int(count) < 1:
        raise QuantacloudCatalogError(
            f"instance type {instance_type!r} is not a '<gpu-slug>:<count>' "
            'QuantaCloud token')
    return slug, int(count)


def _offer_row(offer: Dict[str, Any]) -> Optional[List[Any]]:
    """One offer -> one CSV row, or None when the offer is not lane-rentable."""
    gpu = offer.get('gpu') or {}
    slug = str(gpu.get('slug') or '').strip()
    gpu_count = gpu.get('count') or 0
    if not slug or not isinstance(gpu_count, int) or gpu_count < 1:
        # Bogus row: skip rather than emit garbage.
        return None
    if quantacloud_api.MIG_SLUG_MARKER in slug:
        return None  # a MIG slice must never price as the whole GPU
    if not offer.get('isAvailable', False):
        # The endpoint lists only in-stock offers, but a provider that
        # starts returning stale rows must not reach the catalog.
        return None
    accelerator = normalise_accelerator(slug)
    if not accelerator:
        return None
    raw_price = offer.get('priceHourly')
    if not isinstance(raw_price, (int, float, str)):
        return None  # unpriceable: skip rather than quote as free
    try:
        price = float(raw_price)
    except ValueError:
        return None
    specs = offer.get('specs') or {}
    vcpus = float(specs.get('vcpu') or 0)
    mem_gib = float(specs.get('ram') or 0)
    region = str(offer.get('region') or '').strip()
    if not region:
        return None
    vram = gpu.get('vramGB')
    # JSON with double quotes swapped for single quotes, the convention the
    # other fetchers use for the GpuInfo column.
    gpu_info = json.dumps({
        'AcceleratorName': accelerator,
        'AcceleratorCount': int(gpu_count),
        'MemoryInfo': {
            'SizeInMiB': int(vram * 1024) if vram else 0
        },
    }).replace('"', "'")
    return [
        instance_type_token(slug, int(gpu_count)),
        accelerator,
        int(gpu_count),
        vcpus,
        mem_gib,
        price,
        region,
        gpu_info,
        '',  # no spot tier on QuantaCloud
    ]


def iter_rows(offers: Iterable[Dict[str, Any]]) -> Iterator[List[Any]]:
    """Yield one CSV row per in-stock (slug, count, region) we will rent.

    When the live list carries several offers for the same (slug, count,
    region) at different prices, the CHEAPEST wins: the row is a rentable
    quote, and the provisioner re-resolves against the live list at launch
    anyway (a stale-cheap row must not quote a price the launch refuses,
    and a stale-expensive row would hide real capacity).
    """
    best: Dict[Tuple[str, int, str], List[Any]] = {}
    for offer in offers:
        row = _offer_row(offer)
        if row is None:
            continue
        key = (str(row[0]).rsplit(':', 1)[0], int(row[2]), str(row[6]))
        existing = best.get(key)
        if existing is None or float(row[5]) < float(existing[5]):
            best[key] = row
    yield from best.values()


def verify_zero_stock(offers: List[Dict[str, Any]],
                      families: List[Dict[str, Any]]) -> None:
    """Cross-check an empty offers list before it is written as zero-stock.

    The ruling (ENG-515): an UNFILTERED zero-row fetch cross-checked
    against ``GET /gpu-families`` — if every family reports
    ``availableCount == 0``, the empty is honest (write the header-only
    catalog, the Latitude semantics); if ANY family reports stock while
    the offers list is empty, that is a contradiction (filter bug or API
    drift) and the fetch REFUSES rather than write "no capacity"
    (the Spheron filter-bug semantics).
    """
    if offers:
        return  # nothing to verify: stock exists
    stocked = [
        str(f.get('key') or f.get('label') or '?')
        for f in families
        if _family_available_count(f) > 0
    ]
    if stocked:
        raise QuantacloudCatalogError(
            'offers list is empty but gpu-families reports in-stock '
            f'offers for {sorted(stocked)}; refusing to write a zero-stock '
            'catalog from a contradictory fetch (filter bug or API drift — '
            'investigate before the lane answers "no capacity")')


def _family_available_count(family: Dict[str, Any]) -> int:
    value = family.get('availableCount')
    if isinstance(value, bool) or not isinstance(value, int):
        # availableCount: null (no stock) and non-numeric values are both
        # "no stock" here; a contradictory fetch must come from a POSITIVE
        # integer, not from a missing field.
        return 0
    return value


def fetch_offers(api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """The live in-stock offers (public endpoint; key unused for it)."""
    client = quantacloud_api.QuantacloudClient(api_key or 'public-catalog')
    return client.list_offers()


def fetch_families(api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """The live GPU families (public endpoint; key unused for it)."""
    client = quantacloud_api.QuantacloudClient(api_key or 'public-catalog')
    return client.list_gpu_families()


def write_csv(rows: Iterable[List[Any]], out_path: str) -> int:
    written = 0
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with open(out_path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row)
            written += 1
    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-key-env', default=quantacloud_api.API_KEY_ENV)
    parser.add_argument('--output', default='quantacloud/vms.csv')
    parser.add_argument('--from-fixture',
                        help='read a recorded payload instead of the network')
    args = parser.parse_args(argv)

    if args.from_fixture:
        with open(args.from_fixture, encoding='utf-8') as handle:
            fixture = json.load(handle)
        offers = fixture['offers']
        families = fixture.get('families', [])
    else:
        # The catalog endpoints are PUBLIC: the key is read only so the
        # authenticated endpoints stay reachable from the same client for
        # callers that pass one, but an unset key must NOT stop the fetch
        # (unlike Latitude, whose /plans needs auth). An unreachable API
        # raises from the client (fail loud), it never yields an empty list.
        api_key = os.environ.get(args.api_key_env, '').strip() or None
        offers = fetch_offers(api_key)
        families = fetch_families(api_key)

    verify_zero_stock(offers, families)
    count = write_csv(iter_rows(offers), args.output)
    print(f'wrote {count} rows to {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
