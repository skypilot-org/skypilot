"""Vast cloud adaptor."""

import dataclasses
import functools
import math
import re
from typing import Any, Dict, Optional, Tuple

from sky.utils import annotations

_vast_sdk = None
_COUNTRY_CODE_PATTERN = re.compile(r'^[A-Za-z]{2}$')
_MIN_RELIABILITY = 0.99
_MIN_NETWORK_BANDWIDTH_MBPS = 1000


@dataclasses.dataclass(frozen=True)
class VastOfferRequirements:
    """Requirements that a live Vast offer must satisfy."""

    gpu_name: str
    num_gpus: int
    cpu_cores: int
    cpu_ram_mib: int
    disk_size: int
    country_code: Optional[str]
    datacenter_only: bool
    reliable_hosts: bool
    network_tier: str


@dataclasses.dataclass(frozen=True)
class LiveOfferSnapshot:
    """The request-scoped Vast marketplace response and any query failure."""

    offers: Tuple[Dict[str, Any], ...]
    error: Optional[str]


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _vast_sdk

        if _vast_sdk is None:
            try:
                # isort: off
                from vastai.sdk import VastAI  # pylint: disable=import-outside-toplevel
                # isort: on
                _vast_sdk = VastAI()
            except ImportError as e:
                raise ImportError(f'Fail to import dependencies for vast: {e}\n'
                                  'Try pip install "skypilot[vast]"') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def vast():
    """Return the vast package."""
    return _vast_sdk


def extract_country_code(region: Optional[str]) -> Optional[str]:
    """Return a country code from a normalized or raw Vast catalog region.

    Vast raw regions use ``locality, country, continent``.  The continent is
    also a two-letter code, so accepting a trailing code would silently turn
    ``France, FR, EU`` into ``EU``.  Reject malformed values instead.
    """
    if region is None:
        return None
    if not isinstance(region, str):
        raise ValueError(f'Vast region must be a string, got {region!r}.')

    if region.strip().lower() == 'any':
        return None

    normalized_region = region.strip()
    if _COUNTRY_CODE_PATTERN.fullmatch(normalized_region):
        return normalized_region.upper()

    parts = [part.strip() for part in normalized_region.split(',')]
    if len(parts) >= 2 and _COUNTRY_CODE_PATTERN.fullmatch(parts[-2]):
        return parts[-2].upper()
    raise ValueError('Vast region must be a two-letter country code or a raw '
                     '"locality, country, continent" value; '
                     f'could not extract a country from {region!r}.')


def _normalize_gpu_name(gpu_name: Any) -> str:
    """Normalize equivalent space and underscore GPU spellings."""
    return str(gpu_name or '').replace('_', ' ').strip().casefold()


def _minimum_offer_value(offer: Dict[str, Any], key: str,
                         minimum: float) -> bool:
    """Return whether an offer has a finite numeric value at least minimum."""
    try:
        value = float(offer[key])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(value) and value >= minimum


def _is_true(offer: Dict[str, Any], key: str) -> bool:
    """Interpret Vast boolean fields without accepting arbitrary values."""
    value = offer.get(key)
    return value is True or value == 1 or value == 'true'


def get_offer_requirements(instance_type: str, region: Optional[str],
                           disk_size: int, datacenter_only: bool,
                           reliable_hosts: bool,
                           network_tier: Any) -> VastOfferRequirements:
    """Parse a stable Vast instance type into its live-offer requirements."""
    parts = instance_type.split('-')
    try:
        if not parts[0].endswith('x'):
            raise ValueError
        num_gpus = int(parts[0][:-1])
        cpu_cores = int(parts[-2])
        cpu_ram_mib = int(parts[-1])
        normalized_disk_size = int(disk_size)
    except (IndexError, ValueError) as exc:
        raise ValueError(
            f'Invalid Vast instance type {instance_type!r}.') from exc
    gpu_name = '-'.join(parts[1:-2]).replace('_', ' ')
    if (not gpu_name or
            min(num_gpus, cpu_cores, cpu_ram_mib, normalized_disk_size) <= 0):
        raise ValueError(f'Invalid Vast instance type {instance_type!r}.')

    normalized_network_tier = str(getattr(network_tier, 'value',
                                          network_tier)).lower()
    if normalized_network_tier not in {'standard', 'best'}:
        raise ValueError(
            f'Invalid Vast network tier {network_tier!r}; expected standard '
            'or best.')
    return VastOfferRequirements(
        gpu_name=gpu_name,
        num_gpus=num_gpus,
        cpu_cores=cpu_cores,
        cpu_ram_mib=cpu_ram_mib,
        disk_size=normalized_disk_size,
        country_code=extract_country_code(region),
        datacenter_only=datacenter_only,
        reliable_hosts=reliable_hosts,
        network_tier=normalized_network_tier,
    )


def build_offer_query(requirements: VastOfferRequirements) -> str:
    """Build an SDK-safe final query equivalent to live-offer matching."""
    # Vast SDK 1.5.0 preprocesses query values with an alphanumeric parser.
    # Use integral GiB for its cpu_ram filter, then validate exact MiB values
    # from returned offers in offer_matches_requirements().
    query = [
        'chunked=true',
        'georegion=true',
        f'disk_space>={requirements.disk_size}',
        f'num_gpus={requirements.num_gpus}',
        f'gpu_name={requirements.gpu_name.replace(" ", "_")}',
        f'cpu_cores>={requirements.cpu_cores}',
        f'cpu_ram>={math.ceil(requirements.cpu_ram_mib / 1024)}',
    ]
    if requirements.country_code is not None:
        query.insert(2, f'geolocation={requirements.country_code}')
    if requirements.datacenter_only:
        query.extend(['datacenter=true', 'hosting_type>=1'])
    if requirements.reliable_hosts:
        query.extend([
            'verified=true',
            'datacenter=true',
            'hosting_type>=1',
            f'inet_down>={_MIN_NETWORK_BANDWIDTH_MBPS}',
        ])
    if requirements.network_tier == 'best':
        if not requirements.reliable_hosts:
            query.append(f'inet_down>={_MIN_NETWORK_BANDWIDTH_MBPS}')
        query.append(f'inet_up>={_MIN_NETWORK_BANDWIDTH_MBPS}')
    return ' '.join(query)


def offer_matches_requirements(offer: Any,
                               requirements: VastOfferRequirements) -> bool:
    """Return whether a live offer satisfies every SkyPilot Vast policy."""
    if not isinstance(offer, dict):
        return False
    try:
        num_gpus = int(offer['num_gpus'])
    except (KeyError, TypeError, ValueError):
        return False
    if (_normalize_gpu_name(offer.get('gpu_name')) != _normalize_gpu_name(
            requirements.gpu_name) or num_gpus != requirements.num_gpus):
        return False
    if not all((
            _minimum_offer_value(offer, 'cpu_cores', requirements.cpu_cores),
            _minimum_offer_value(offer, 'cpu_ram', requirements.cpu_ram_mib),
            _minimum_offer_value(offer, 'disk_space', requirements.disk_size),
    )):
        return False
    if requirements.country_code is not None:
        try:
            offer_country = extract_country_code(offer.get('geolocation'))
        except ValueError:
            return False
        if offer_country != requirements.country_code:
            return False

    requires_datacenter = (requirements.datacenter_only or
                           requirements.reliable_hosts)
    if (requires_datacenter and
        (not _is_true(offer, 'datacenter') or
         not _minimum_offer_value(offer, 'hosting_type', 1))):
        return False
    if requirements.reliable_hosts:
        if (not _is_true(offer, 'verified') or
                not _minimum_offer_value(offer, 'reliability', _MIN_RELIABILITY)
                or not _minimum_offer_value(offer, 'inet_down',
                                            _MIN_NETWORK_BANDWIDTH_MBPS)):
            return False
    if requirements.network_tier == 'best':
        if (not _minimum_offer_value(offer, 'inet_down',
                                     _MIN_NETWORK_BANDWIDTH_MBPS) or
                not _minimum_offer_value(offer, 'inet_up',
                                         _MIN_NETWORK_BANDWIDTH_MBPS)):
            return False
    return True


@annotations.lru_cache(scope='request', maxsize=1)
def get_live_offer_snapshot() -> LiveOfferSnapshot:
    """Fetch one broad live Vast offer snapshot for the current request."""
    try:
        offers = vast().search_offers(query='chunked=true georegion=true',
                                      order='dph_total',
                                      limit=10000)
    except Exception as exc:  # pylint: disable=broad-except
        return LiveOfferSnapshot(
            offers=(),
            error=(f'{type(exc).__name__}: {exc}'),
        )
    if not isinstance(offers, list):
        return LiveOfferSnapshot(
            offers=(),
            error=('Vast returned an unexpected live-offer response.'),
        )
    return LiveOfferSnapshot(
        offers=tuple(offer for offer in offers if isinstance(offer, dict)),
        error=None,
    )
