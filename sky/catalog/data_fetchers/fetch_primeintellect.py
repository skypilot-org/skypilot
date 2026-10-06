"""Fetch Prime Intellect GPU offers and emit a SkyPilot catalog CSV.

One row per in-stock (upstream provider, GPU type, count, dataCenter)
quad the lane can actually rent, from ``GET /availability/gpus``:

* **Unfiltered fetch, local filter.** No server-side filters are applied
  (a filtered empty is ambiguous by construction — the quanta lesson),
  so an empty result can only mean "sold out"... except:
* **The empty-200 replica flake.** This endpoint INTERMITTENTLY serves
  ``200 {"items": [], "totalCount": 0}`` off a flaky replica (observed
  2026-10-06: rows vanished and repopulated on retry within seconds).
  The client retries the empty signature a bounded number of times, and
  a STILL-empty fetch is cross-checked here against
  ``GET /availability/gpu-summary`` — an independent code path — before
  anything is written: the summary pricing ANY family while offers says
  empty is a contradiction (refuse and raise, the Spheron filter-bug
  semantics); both empty is honest zero-stock (header-only write, the
  Latitude semantics).
* **VM-class upstream allowlist.** The deployment model is
  PER-UPSTREAM-PROVIDER: Prime's own ``dc_*`` datacenters and
  ``primecompute`` boot full-OS VMs (the SkyPilot bootstrap ladder);
  container-shaped upstreams (notably runpod) do not. Rows for
  non-VM-class upstreams are dropped — the lane must never rent a box
  that cannot run the ladder.
* **Region is the offer's dataCenter token** (``us-east-1``,
  ``us-central-2``): the controller's residency filter exact-matches
  ``Region`` against budget ``allowedRegions`` tokens, and the
  provisioner re-resolves offers by the same token (the create body's
  ``dataCenterId`` takes exactly it).
* **Price is the whole-box ``prices.onDemand``** (a 2-GPU offer's hourly
  price is for the entire box; the 2-GPU Blackwell box is $2.70 = 2 x
  $1.35/GPU).
* **InstanceType is ``<provider>:<gpuType>:<count>``** — offers are a
  live market and ``cloudId`` values are upstream plan tokens, so the
  stable triple is the catalog identity and the provisioner re-resolves
  the concrete offer (cloudId + provider + country) at launch. Multiple
  live offers for the same triple collapse to the CHEAPEST row.
* Offers without a usable price are skipped: an unpriceable row would
  quote as free (fail closed, not silent).

SSH is implicit on every pod (account keys are injected at deploy time;
the docs example is ``root@<ip> -p 22``), so there is no per-offer ``ssh``
feature to check.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from sky.adaptors import primeintellect as primeintellect_api

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

# GPU type -> the ONE accelerator token the platform's identities use for
# that family (see _PROVIDER_GPU_IDENTITIES in skypilot-controller). Only
# the 6000-class families carry explicit mappings (the shape vocabulary
# our identities request); everything else passes through compacted
# (non-alphanumerics stripped, uppercased) so an unrecognized real
# accelerator stays visible in the catalog for audit instead of being
# silently hidden — nobody requests it through our identities.
_ACCELERATOR_MAP = {
    'RTX_PRO_6000B_96GB': 'RTXPRO6000',
    'RTX6000Ada_48GB': 'RTX6000ADA',
    'A6000_48GB': 'RTXA6000',
}


class PrimeIntellectCatalogError(RuntimeError):
    """Raised when the fetcher cannot run or the payload is unusable."""


def normalise_accelerator(gpu_type: Optional[str]) -> str:
    gpu_type = str(gpu_type or '').strip()
    if not gpu_type:
        return ''
    if gpu_type in _ACCELERATOR_MAP:
        return _ACCELERATOR_MAP[gpu_type]
    return ''.join(c for c in gpu_type.upper() if c.isalnum())


def instance_type_token(provider: str, gpu_type: str, gpu_count: int) -> str:
    """The stable catalog identity: ``<provider>:<gpuType>:<count>``.

    Deliberately NOT the cloudId: cloudIds are upstream plan tokens on a
    live market (the probe watched the RTX PRO 6000 list flip 2->0->2
    offers within minutes), while the (provider, gpuType, count) triple
    is what the provisioner re-resolves against at launch.
    """
    return f'{provider}:{gpu_type}:{gpu_count}'


def parse_instance_type_token(instance_type: str) -> Tuple[str, str, int]:
    """The inverse of ``instance_type_token``; raises on garbage."""
    parts = str(instance_type).split(':')
    if len(parts) != 3:
        raise PrimeIntellectCatalogError(
            f'instance type {instance_type!r} is not the Prime Intellect '
            '"<provider>:<gpuType>:<count>" token')
    provider, gpu_type, count = parts
    try:
        gpu_count = int(count)
    except ValueError as exc:
        raise PrimeIntellectCatalogError(
            f'instance type {instance_type!r} carries a non-integer count '
            f'{count!r}') from exc
    if gpu_count < 1:
        raise PrimeIntellectCatalogError(
            f'instance type {instance_type!r} carries a non-positive count')
    return provider, gpu_type, gpu_count


def _default(resource: Optional[Dict[str, Any]]) -> float:
    """A resource block's defaultCount as a float (0 when absent)."""
    if isinstance(resource, dict):
        value = resource.get('defaultCount')
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    return 0.0


def _offer_row(offer: Dict[str, Any]) -> Optional[List[Any]]:
    """One offer -> one CSV row, or None when not lane-rentable."""
    provider = str(offer.get('provider') or '').strip()
    gpu_type = str(offer.get('gpuType') or '').strip()
    gpu_count = offer.get('gpuCount')
    if not provider or not gpu_type:
        return None  # bogus row: skip rather than emit garbage
    if not isinstance(gpu_count, int) or gpu_count < 1:
        return None
    # THE deployment-model filter: the VM-class upstream allowlist (see
    # module docstring). A container-shaped upstream cannot run the
    # bootstrap ladder, so its rows must never price as rentable.
    if not primeintellect_api.is_vm_class_upstream(provider):
        return None
    stock = str(offer.get('stockStatus') or '').lower()
    if stock and stock not in ('available', 'low'):
        return None  # an unknown/unavailable stock flag is not rentable
    data_center = str(offer.get('dataCenter') or '').strip()
    if not data_center:
        return None  # no dataCenter token: the create body cannot be built
    prices = offer.get('prices')
    raw_price = prices.get('onDemand') if isinstance(prices, dict) else None
    if not isinstance(raw_price, (int, float)):
        return None  # unpriceable: skip rather than quote as free
    price = float(raw_price)
    accelerator = normalise_accelerator(gpu_type)
    if not accelerator:
        return None
    gpu_memory = offer.get('gpuMemory')
    vram_per_gpu = 0.0
    if isinstance(gpu_memory, (int, float)) and gpu_memory > 0:
        # gpuMemory is the TOTAL across the box (the 2-GPU Blackwell
        # offer reports 192); per-GPU VRAM is what GpuInfo must carry.
        vram_per_gpu = float(gpu_memory) / gpu_count
    gpu_info = json.dumps({
        'AcceleratorName': accelerator,
        'AcceleratorCount': int(gpu_count),
        'MemoryInfo': {
            'SizeInMiB': int(vram_per_gpu * 1024) if vram_per_gpu else 0
        },
    }).replace('"', "'")
    return [
        instance_type_token(provider, gpu_type, int(gpu_count)),
        accelerator,
        int(gpu_count),
        _default(offer.get('vcpu')),
        _default(offer.get('memory')),
        price,
        data_center,
        gpu_info,
        '',  # the lane rents on-demand only (no spot tier)
    ]

def iter_rows(offers: Iterable[Dict[str, Any]]) -> Iterator[List[Any]]:
    """Yield one CSV row per in-stock lane-rentable offer triple.

    When the live list carries several offers for the same (provider,
    gpuType, count, dataCenter) at different prices, the CHEAPEST wins:
    the row is a rentable quote, and the provisioner re-resolves against
    the live list at launch anyway (a stale-cheap row must not quote a
    price the launch refuses, and a stale-expensive row would hide real
    capacity).
    """
    best: Dict[Tuple[str, str, int, str], List[Any]] = {}
    for offer in offers:
        row = _offer_row(offer)
        if row is None:
            continue
        provider, gpu_type, count = parse_instance_type_token(str(row[0]))
        key = (provider, gpu_type, count, str(row[6]))
        existing = best.get(key)
        if existing is None or float(row[5]) < float(existing[5]):
            best[key] = row
    yield from best.values()


def _entry_priced(entry: Any) -> bool:
    """Whether a count entry carries a positive onDemand price.

    Two shapes: a flat ``{"onDemand": 1.35}`` (future-proofing) and the
    observed nested one (probed 2026-10-06) where the price sits one
    level down — ``{"cheapest": {"onDemand": 1.35}, "united_states":
    {"onDemand": 1.35}}``. Both count.
    """
    if not isinstance(entry, dict):
        return False
    price = entry.get('onDemand')
    if isinstance(price, (int, float)) and price > 0:
        return True
    for sub in entry.values():
        if isinstance(sub, dict):
            price = sub.get('onDemand')
            if isinstance(price, (int, float)) and price > 0:
                return True
    return False

def _summary_priced(summary: Dict[str, Any]) -> List[str]:
    """Families the gpu-summary endpoint prices (any count, any region).

    The summary's per-count entries look like ``{"1": {"cheapest":
    {"onDemand": 1.35, ...}, "united_states": {...}}}``; a priced entry is
    one whose ``onDemand`` is a positive number. This is the independent
    second opinion for the zero-stock cross-check.
    """
    priced: List[str] = []
    if not isinstance(summary, dict):
        return priced
    for family, per_count in summary.items():
        if not isinstance(per_count, dict):
            continue
        if any(_entry_priced(entry) for entry in per_count.values()):
            priced.append(str(family))
    return priced


def verify_zero_stock(offers: List[Dict[str, Any]],
                      summary: Optional[Dict[str, Any]]) -> None:
    """Cross-check an empty offers list before it is written as zero-stock.

    The binding (orchestrator ruling 2026-10-06): a 200-empty from
    ``/availability/gpus`` is NOT honest zero-stock until cross-checked
    against an independent endpoint — this broker serves that empty
    signature off a flaky replica. The client already retried the empty
    fetch; here, an offers list that is STILL empty is checked against
    ``/availability/gpu-summary``:

    * the summary prices ANY family while offers says empty -> the
      endpoints DISAGREE: refuse and raise (the Spheron filter-bug
      semantics — never write a blank catalog off a flaky replica);
    * the summary also prices nothing -> both endpoints agree on empty:
      honest zero-stock (the caller writes the header-only catalog, the
      Latitude semantics).
    """
    if offers:
        return  # stock exists; nothing to verify
    if summary is None:
        # No second opinion available: treat an unverified empty as a
        # fetch failure, never as a sellout.
        raise PrimeIntellectCatalogError(
            'Prime Intellect offers list is empty and the gpu-summary '
            'cross-check is unavailable; refusing to write zero-stock '
            'without the second opinion (the flaky-replica empty-200 was '
            'observed 2026-10-06)')
    priced = _summary_priced(summary)
    if priced:
        raise PrimeIntellectCatalogError(
            'Prime Intellect endpoints DISAGREE: /availability/gpus '
            f'returned 0 offers (after retries) but /availability/'
            f'gpu-summary prices {len(priced)} family/families '
            f'({", ".join(sorted(priced)[:5])}...); this is the flaky '
            'replica, not a sellout — investigate before the lane answers '
            '"no capacity"')


def _resolve_key(env_name: str) -> Optional[str]:
    """The API key from the env or the credential file (the availability
    endpoints are authenticated on this provider, unlike QuantaCloud's
    public catalog)."""
    key = os.environ.get(env_name, '').strip()
    if not key:
        path = os.path.expanduser(primeintellect_api.API_KEY_FILE)
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as handle:
                key = handle.read().strip()
    return key or None


def fetch_offers(api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """The live in-stock offers, unfiltered (the client retries the
    empty-200 replica signature internally)."""
    client = primeintellect_api.PrimeIntellectClient(
        api_key or _resolve_key(primeintellect_api.API_KEY_ENV) or '')
    return client.list_offers()


def fetch_summary(api_key: Optional[str] = None) -> Dict[str, Any]:
    """The live gpu-summary (the zero-stock cross-check)."""
    client = primeintellect_api.PrimeIntellectClient(
        api_key or _resolve_key(primeintellect_api.API_KEY_ENV) or '')
    return client.get_gpu_summary()


def write_csv(rows: Iterable[List[Any]], out_path: str) -> int:
    written = 0
    directory = os.path.dirname(out_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(out_path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row)
            written += 1
    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-key-env', default=primeintellect_api.API_KEY_ENV)
    parser.add_argument('--output', default='primeintellect/vms.csv')
    parser.add_argument('--from-fixture',
                        help='read a recorded payload instead of the network')
    args = parser.parse_args(argv)

    if args.from_fixture:
        with open(args.from_fixture, encoding='utf-8') as handle:
            fixture = json.load(handle)
        offers = fixture['offers']
        summary = fixture.get('summary')
    else:
        # The availability endpoints are authenticated; the key comes from
        # the env or the credential file. An unreachable API raises from
        # the client (fail loud), it never yields an empty list that would
        # read as "no capacity".
        api_key = _resolve_key(args.api_key_env)
        if not api_key:
            raise PrimeIntellectCatalogError(
                f'{args.api_key_env} is not set and '
                f'{primeintellect_api.API_KEY_FILE} does not exist; the '
                'Prime Intellect catalog is authenticated — refusing to '
                'fetch blind')
        offers = fetch_offers(api_key)
        summary = None
        if not offers:
            # Only fetch the cross-check when it can change the verdict.
            summary = fetch_summary(api_key)

    verify_zero_stock(offers, summary)
    count = write_csv(iter_rows(offers), args.output)
    print(f'wrote {count} rows to {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
