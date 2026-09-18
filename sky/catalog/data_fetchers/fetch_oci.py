"""A script that generates the OCI catalog (``oci/vms.csv``).

Unlike most cloud fetchers this one needs no cloud credentials: Oracle
publishes both the shape specifications and the price list.

Data sources:

1. Shape specifications: the JSON behind the public OCI Cost Estimator
   (https://www.oracle.com/cloud/costestimator.html). For every shape it
   lists the GPU count, GPU memory, OCPUs, memory, whether preemptible
   capacity is offered, and the part numbers the shape is billed under.
2. Prices: the public OCI price list API
   (https://www.oracle.com/cloud/price-list/), which maps part numbers to
   PAY_AS_YOU_GO prices per currency. OCI prices are the same in every
   commercial region.

Regional availability of shapes is not published by Oracle, and the
authenticated API (``ComputeClient.list_shapes``) only returns the shapes a
tenancy is entitled to launch (a free-tier tenancy sees no GPU shapes at
all). By default the catalog therefore lists every shape in every commercial
region, exactly like the hand-maintained catalog did since 2023. This is safe
because ``sky/catalog/oci_catalog.py`` filters the catalog down to the
regions the user's tenancy is subscribed to, and provisioning fails over to
the next zone/region when a shape has no capacity.

Pass ``--use-sdk`` to derive regions, availability domains and per-AD shape
availability from the tenancy configured in ``~/.oci/config`` instead. The
result is a lower bound: only subscribed regions, and only the shapes that
tenancy is entitled to, are kept.

Usage:
    python -m sky.catalog.data_fetchers.fetch_oci
    python -m sky.catalog.data_fetchers.fetch_oci --regions us-ashburn-1
    python -m sky.catalog.data_fetchers.fetch_oci --use-sdk --profile MYPROF
"""
import argparse
import csv
import dataclasses
import json
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Set

import requests

logger = logging.getLogger(__name__)

SHAPES_URL = ('https://www.oracle.com/a/ocom/docs/cloudestimator2/data/'
              'shapes.json')
PRICE_LIST_URL = 'https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/'
REQUEST_TIMEOUT_SECONDS = 60

CURRENCY = 'USD'
PRICE_MODEL = 'PAY_AS_YOU_GO'

# Preemptible instances are billed at 50% of the on-demand price:
# https://docs.oracle.com/en-us/iaas/Content/Compute/Concepts/preemptible.htm
PREEMPTIBLE_DISCOUNT = 0.5

# Commercial (OC1 realm) regions and their number of availability domains.
# Source:
# https://docs.oracle.com/en-us/iaas/Content/General/Concepts/regions.htm
# The catalog encodes zones as '<region>-AD-<n>'; the tenancy-specific prefix
# of the real AD name is added by sky/clouds/oci.py at launch time.
REGIONS: Dict[str, int] = {
    'af-casablanca-1': 1,
    'af-johannesburg-1': 1,
    'ap-batam-1': 1,
    'ap-chuncheon-1': 1,
    'ap-hyderabad-1': 1,
    'ap-kulai-2': 1,
    'ap-melbourne-1': 1,
    'ap-mumbai-1': 1,
    'ap-osaka-1': 1,
    'ap-seoul-1': 1,
    'ap-singapore-1': 1,
    'ap-singapore-2': 1,
    'ap-sydney-1': 1,
    'ap-tokyo-1': 1,
    'ca-montreal-1': 1,
    'ca-toronto-1': 1,
    'eu-amsterdam-1': 1,
    'eu-frankfurt-1': 3,
    'eu-madrid-1': 1,
    'eu-madrid-3': 1,
    'eu-marseille-1': 1,
    'eu-milan-1': 1,
    'eu-paris-1': 1,
    'eu-stockholm-1': 1,
    'eu-turin-1': 1,
    'eu-zurich-1': 1,
    'il-jerusalem-1': 1,
    'me-abudhabi-1': 1,
    'me-dubai-1': 1,
    'me-jeddah-1': 1,
    'me-riyadh-1': 1,
    'mx-monterrey-1': 1,
    'mx-queretaro-1': 1,
    'sa-bogota-1': 1,
    'sa-santiago-1': 1,
    'sa-saopaulo-1': 1,
    'sa-valparaiso-1': 1,
    'sa-vinhedo-1': 1,
    'uk-cardiff-1': 1,
    'uk-london-1': 3,
    'us-ashburn-1': 3,
    'us-chicago-1': 3,
    'us-phoenix-1': 3,
    'us-sanjose-1': 1,
}

# CPU shapes to include. Flexible shapes are catalogued as
# '<shape>$_<vcpus>_<memory_gib>' (see sky/clouds/utils/oci_utils.py); the
# (vCPU, memory) grid below is the one the catalog has always offered. The
# default-instance selection in oci_catalog.py picks the cheapest of these,
# so adding a family here changes which shape `sky launch` uses by default.
CPU_FLEX_SHAPES = ('VM.Standard.E4.Flex', 'VM.Standard.E5.Flex')
FLEX_VCPUS = (2, 4, 6, 8, 12, 16, 20, 24, 32, 64, 96, 128)
FLEX_MEMORY_PER_VCPU_GIB = (4, 8)

# x86 OCPUs are hyperthreaded cores: 1 OCPU = 2 vCPUs. Arm OCPUs (Ampere,
# NVIDIA Grace) have no SMT: 1 OCPU = 1 vCPU.
VCPUS_PER_X86_OCPU = 2

# Shape-name token -> SkyPilot accelerator name, for shapes whose name does
# not spell out the GPU model. Names follow the other SkyPilot catalogs (see
# common/accelerators.csv in skypilot-catalog), e.g. 'RTXPRO6000' not
# 'RTX PRO 6000'.
ACCELERATOR_NAME_OVERRIDES = {
    'GPU2': 'P100',
    'GPU3': 'V100',
    'GPU4': 'A100',
    'A100-v2': 'A100-80GB',
    'RTXPRO': 'RTXPRO6000',
}
_GPU_SHAPE_RE = re.compile(
    r'^(?:BM|VM)\.(GPU\d?)(?:\.([A-Za-z0-9-]+))?\.(\d+)$')

# Per-GPU memory in GB, from the OCI shape documentation:
# https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm
# The estimator's 'gpuMemoryQty' is the shape total for most shapes but the
# per-GPU figure for a few (BM.GPU.MI355X.8, BM.GPU.GB300.4), so it is only
# used as a cross-check here, and as the fallback for GPUs not listed.
GPU_MEMORY_GB: Dict[str, float] = {
    'P100': 16,
    'V100': 16,
    'A10': 24,
    'A100': 40,
    'A100-80GB': 80,
    'H100': 80,
    'H200': 141,
    'L40S': 48,
    'B200': 180,
    'B300': 262.5,  # 2100 GB / 8 per the OCI shape table.
    'GB200': 192,
    'GB300': 278,
    'RTXPRO6000': 96,
    'MI300X': 192,
    'MI355X': 288,
}


@dataclasses.dataclass(frozen=True)
class ShapeSpec:
    ocpus: int
    memory_gb: int
    vcpus_per_ocpu: int = VCPUS_PER_X86_OCPU


# OCPU/memory for shapes the estimator lacks or gets wrong: it has no CPU or
# memory data for the newest shapes and lists 128 OCPUs for BM.GPU.GB200.4,
# whose two 72-core NVIDIA Grace CPUs give 144. Source: the shape table above.
SHAPE_SPEC_OVERRIDES: Dict[str, ShapeSpec] = {
    'BM.GPU.GB200.4': ShapeSpec(ocpus=144, memory_gb=960, vcpus_per_ocpu=1),
    'BM.GPU.GB300.4': ShapeSpec(ocpus=144, memory_gb=960, vcpus_per_ocpu=1),
    'BM.GPU.B300.8': ShapeSpec(ocpus=128, memory_gb=4096),
    'BM.GPU.RTXPRO.8': ShapeSpec(ocpus=144, memory_gb=3072),
}

# Region -> zone -> shapes offered there; None means every priced shape.
Availability = Dict[str, Dict[str, Optional[Set[str]]]]

CSV_COLUMNS = [
    'InstanceType',
    'AcceleratorName',
    'AcceleratorCount',
    'vCPUs',
    'MemoryGiB',
    'GpuInfo',
    'Price',
    'SpotPrice',
    'Region',
    'AvailabilityZone',
]


@dataclasses.dataclass
class ShapeInfo:
    """One catalog entry, before it is expanded over regions/zones.

    Attributes:
        shape: The OCI shape name, as accepted by the Compute API.
        instance_type: The SkyPilot instance type. Equal to ``shape`` for
            fixed shapes; ``'<shape>$_<vcpus>_<memory>'`` for flexible ones.
        vcpus: Number of vCPUs.
        memory_gib: Host memory in GiB.
        price: Hourly on-demand price in USD.
        spot_price: Hourly preemptible price in USD, or None if the shape
            cannot be launched as preemptible.
        accelerator_name: SkyPilot accelerator name, or None for CPU shapes.
        accelerator_count: Number of GPUs.
        gpu_memory_mib: Memory of a single GPU in MiB.
        manufacturer: GPU vendor, as written into GpuInfo.
    """
    shape: str
    instance_type: str
    vcpus: int
    memory_gib: int
    price: float
    spot_price: Optional[float]
    accelerator_name: Optional[str] = None
    accelerator_count: int = 0
    gpu_memory_mib: int = 0
    manufacturer: Optional[str] = None

    @property
    def is_gpu(self) -> bool:
        return self.accelerator_name is not None

    def gpu_info(self) -> str:
        if not self.is_gpu:
            return ''
        info = {
            'Gpus': [{
                'Name': self.accelerator_name,
                'Manufacturer': self.manufacturer,
                'Count': self.accelerator_count,
                'MemoryInfo': {
                    'SizeInMiB': self.gpu_memory_mib
                },
            }],
            'TotalGpuMemoryInMiB': self.gpu_memory_mib * self.accelerator_count,
        }
        # Same quoting as the other catalogs; parsed with ast.literal_eval.
        return json.dumps(info).replace('"', '\'')


def _fetch_json(url: str) -> Any:
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def load_prices(products: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    """Maps part number -> hourly USD PAY_AS_YOU_GO price."""
    prices: Dict[str, float] = {}
    for product in products:
        for localization in product.get('currencyCodeLocalizations', []):
            if localization.get('currencyCode') != CURRENCY:
                continue
            for price in localization.get('prices', []):
                if price.get('model') == PRICE_MODEL:
                    prices[product['partNumber']] = float(price['value'])
    return prices


def load_price_metrics(products: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """Maps part number -> billing metric, e.g. 'GPU Per Hour'."""
    return {p['partNumber']: p.get('metricName', '') for p in products}


def normalize_shape_name(name: str) -> str:
    """Strips display-only suffixes, e.g. 'BM.GPU.GB200.4 (NVL72)'."""
    return re.sub(r'\s*\(.*\)\s*$', '', name).strip()


def accelerator_name_from_shape(shape: str) -> Optional[str]:
    """Returns the SkyPilot accelerator name encoded in a GPU shape name.

    'BM.GPU.H100.8' -> 'H100', 'VM.GPU3.1' -> 'V100',
    'BM.GPU.A100-v2.8' -> 'A100-80GB', 'VM.Standard.E4.Flex' -> None.
    """
    match = _GPU_SHAPE_RE.match(shape)
    if match is None:
        return None
    token = match.group(2) or match.group(1)
    return ACCELERATOR_NAME_OVERRIDES.get(token, token)


def gpu_manufacturer(accelerator_name: str) -> str:
    return 'AMD' if accelerator_name.startswith('MI') else 'NVIDIA'


def _product(shape: Dict[str, Any], product_type: str) -> Optional[Dict]:
    for product in shape.get('products') or []:
        if (product.get('type') or {}).get('value') == product_type:
            return product
    return None


def gpu_shape_info(shape: Dict[str, Any], prices: Dict[str, float],
                   metrics: Dict[str, str]) -> Optional[ShapeInfo]:
    """Builds the catalog entry for a GPU shape from the estimator JSON."""
    name = normalize_shape_name(shape['name'])
    accelerator = accelerator_name_from_shape(name)
    gpu_count = int(shape.get('gpuQty') or 0)
    if accelerator is None or gpu_count <= 0:
        logger.warning('Skipping %s: not a recognized GPU shape.', name)
        return None

    product = _product(shape, 'ocpu')
    if product is None or product['partNumber'] not in prices:
        logger.warning('Skipping %s: no price for its part number.', name)
        return None
    unit_price = prices[product['partNumber']]
    metric = metrics.get(product['partNumber'], '')

    override = SHAPE_SPEC_OVERRIDES.get(name)
    if override is not None:
        ocpus: Optional[int] = override.ocpus
        memory_gb: Optional[int] = override.memory_gb
        vcpus_per_ocpu = override.vcpus_per_ocpu
    else:
        ocpus = product.get('qty')
        memory_gb = shape.get('bundleMemoryQty')
        vcpus_per_ocpu = VCPUS_PER_X86_OCPU
    if not ocpus or not memory_gb:
        logger.warning(
            'Skipping %s: the estimator has no OCPU/memory data for it and '
            'it is not in SHAPE_SPEC_OVERRIDES.', name)
        return None

    if 'GPU' in metric.upper():
        price = unit_price * gpu_count
    elif 'OCPU' in metric.upper():
        price = unit_price * ocpus
    else:
        logger.warning('Skipping %s: unexpected billing metric %r.', name,
                       metric)
        return None
    if price <= 0:
        logger.warning('Skipping %s: non-positive price %s.', name, price)
        return None

    gpu_memory_gb = GPU_MEMORY_GB.get(accelerator)
    estimator_total_gb = shape.get('gpuMemoryQty')
    if gpu_memory_gb is None:
        if not estimator_total_gb:
            logger.warning('Skipping %s: unknown GPU memory for %s.', name,
                           accelerator)
            return None
        gpu_memory_gb = estimator_total_gb / gpu_count
        logger.warning(
            '%s: %s is not in GPU_MEMORY_GB; using %s GB per GPU derived '
            'from the estimator. Please verify and add it.', name, accelerator,
            gpu_memory_gb)
    elif (estimator_total_gb and
          abs(estimator_total_gb - gpu_memory_gb * gpu_count) > 1):
        logger.info(
            '%s: estimator lists %s GB of GPU memory, catalog uses %s GB '
            '(%s x %s GB).', name, estimator_total_gb,
            gpu_memory_gb * gpu_count, gpu_count, gpu_memory_gb)

    spot_price = None
    if shape.get('allowPreemptible'):
        spot_price = price * PREEMPTIBLE_DISCOUNT
    return ShapeInfo(shape=name,
                     instance_type=name,
                     vcpus=int(ocpus) * vcpus_per_ocpu,
                     memory_gib=int(memory_gb),
                     price=price,
                     spot_price=spot_price,
                     accelerator_name=accelerator,
                     accelerator_count=gpu_count,
                     gpu_memory_mib=int(round(gpu_memory_gb * 1024)),
                     manufacturer=gpu_manufacturer(accelerator))


def flex_shape_infos(shape: Dict[str, Any],
                     prices: Dict[str, float]) -> List[ShapeInfo]:
    """Builds the catalog entries for a flexible CPU shape."""
    name = normalize_shape_name(shape['name'])
    ocpu_product = _product(shape, 'ocpu')
    memory_product = _product(shape, 'memory')
    if (ocpu_product is None or memory_product is None or
            ocpu_product['partNumber'] not in prices or
            memory_product['partNumber'] not in prices):
        logger.warning('Skipping %s: missing OCPU or memory price.', name)
        return []
    ocpu_price = prices[ocpu_product['partNumber']]
    memory_price = prices[memory_product['partNumber']]
    max_ocpus = ocpu_product.get('max') or float('inf')
    max_memory = memory_product.get('max') or float('inf')
    preemptible = bool(shape.get('allowPreemptible'))

    infos = []
    for vcpus in FLEX_VCPUS:
        ocpus = vcpus / VCPUS_PER_X86_OCPU
        for ratio in FLEX_MEMORY_PER_VCPU_GIB:
            memory = vcpus * ratio
            if ocpus > max_ocpus or memory > max_memory:
                continue
            price = ocpus * ocpu_price + memory * memory_price
            infos.append(
                ShapeInfo(shape=name,
                          instance_type=f'{name}$_{vcpus}_{memory}',
                          vcpus=vcpus,
                          memory_gib=memory,
                          price=price,
                          spot_price=price *
                          PREEMPTIBLE_DISCOUNT if preemptible else None))
    return infos


def collect_shapes(shapes_json: Dict[str, Any],
                   products: List[Dict[str, Any]]) -> List[ShapeInfo]:
    """Turns the estimator shapes and the price list into catalog entries."""
    prices = load_prices(products)
    metrics = load_price_metrics(products)
    cpu_infos: List[ShapeInfo] = []
    gpu_infos: List[ShapeInfo] = []
    for shape in shapes_json['items']:
        name = normalize_shape_name(shape['name'])
        if shape.get('status') != 'ACTIVE' or shape.get('hidden'):
            continue
        if not name.startswith(('BM.', 'VM.')):
            continue
        if (shape.get('subType') or {}).get('value') == 'gpu':
            info = gpu_shape_info(shape, prices, metrics)
            if info is not None:
                gpu_infos.append(info)
        elif name in CPU_FLEX_SHAPES:
            cpu_infos.extend(flex_shape_infos(shape, prices))
    gpu_infos.sort(key=lambda s: s.shape)
    cpu_infos.sort(key=lambda s: (s.shape, s.vcpus, s.memory_gib))
    return cpu_infos + gpu_infos


def static_availability(regions: Dict[str, int]) -> Dict[str, List[str]]:
    """Region -> zones, assuming every shape is offered everywhere."""
    return {
        region: [f'{region}-AD-{i}' for i in range(1, num_ads + 1)
                ] for region, num_ads in sorted(regions.items())
    }


def sdk_availability(profile: str,
                     regions: Optional[List[str]] = None) -> Availability:
    """Region -> zone -> shapes the configured tenancy can launch there.

    Requires ``skypilot[oci]`` and a working ``~/.oci/config``. Only the
    tenancy's subscribed regions can be queried, and ``list_shapes`` only
    returns shapes the tenancy is entitled to, so this is a lower bound on
    what OCI offers.
    """
    # pylint: disable=import-outside-toplevel
    from sky.adaptors import oci as oci_adaptor
    oci = oci_adaptor.oci

    tenancy_id = oci_adaptor.get_oci_config(profile=profile)['tenancy']
    identity = oci_adaptor.get_identity_client(profile=profile)
    subscriptions = identity.list_region_subscriptions(tenancy_id).data
    subscribed = sorted(s.region_name for s in subscriptions)
    logger.info('Tenancy is subscribed to: %s', ', '.join(subscribed))

    availability: Availability = {}
    for region in subscribed:
        if regions is not None and region not in regions:
            continue
        identity = oci_adaptor.get_identity_client(region=region,
                                                   profile=profile)
        compute = oci_adaptor.get_core_client(region=region, profile=profile)
        ads = identity.list_availability_domains(compartment_id=tenancy_id).data
        availability[region] = {}
        for ad in ads:
            match = re.search(r'-AD-(\d+)$', ad.name)
            if match is None:
                logger.warning('Skipping unrecognized AD name in %s.', region)
                continue
            zone = f'{region}-AD-{match.group(1)}'
            shapes = oci.pagination.list_call_get_all_results(
                compute.list_shapes,
                compartment_id=tenancy_id,
                availability_domain=ad.name).data
            visible = {s.shape for s in shapes}
            availability[region][zone] = visible
            logger.info('%s: %d shapes visible.', zone, len(visible))
    return availability


def expand_rows(shapes: List[ShapeInfo],
                availability: Availability) -> List[Dict[str, Any]]:
    """Cross-product of shapes and zones; ``None`` means every shape."""
    rows = []
    for region in sorted(availability):
        for zone in sorted(availability[region]):
            visible = availability[region][zone]
            for shape in shapes:
                if visible is not None and shape.shape not in visible:
                    continue
                rows.append({
                    'InstanceType': shape.instance_type,
                    'AcceleratorName': shape.accelerator_name or '',
                    'AcceleratorCount': shape.accelerator_count or '',
                    'vCPUs': shape.vcpus,
                    'MemoryGiB': shape.memory_gib,
                    'GpuInfo': shape.gpu_info(),
                    'Price': _format_price(shape.price),
                    'SpotPrice': _format_price(shape.spot_price),
                    'Region': region,
                    'AvailabilityZone': zone,
                })
    return rows


def _format_price(price: Optional[float]) -> str:
    if price is None:
        return ''
    return f'{price:.6f}'.rstrip('0').rstrip('.')


def write_csv(rows: List[Dict[str, Any]], output_path: str) -> None:
    dirname = os.path.dirname(output_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '--use-sdk',
        action='store_true',
        help='Derive regions, zones and per-zone shape availability from '
        'the tenancy in ~/.oci/config instead of listing every shape in '
        'every commercial region. Requires skypilot[oci].')
    parser.add_argument('--profile',
                        default=os.environ.get('OCI_CLI_PROFILE', 'DEFAULT'),
                        help='Profile in ~/.oci/config used with --use-sdk '
                        '(default: $OCI_CLI_PROFILE or DEFAULT).')
    parser.add_argument(
        '--regions',
        nargs='+',
        help='Only emit these regions. Without --use-sdk, regions not in '
        'the built-in list are assumed to have one availability domain.')
    parser.add_argument('--output', default='oci/vms.csv')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    logger.info('Fetching shape specifications from %s', SHAPES_URL)
    shapes_json = _fetch_json(SHAPES_URL)
    logger.info('Fetching price list from %s', PRICE_LIST_URL)
    products = _fetch_json(PRICE_LIST_URL)['items']
    shapes = collect_shapes(shapes_json, products)
    num_gpu_shapes = sum(1 for s in shapes if s.is_gpu)
    num_cpu_shapes = len(shapes) - num_gpu_shapes
    logger.info('Priced %d GPU shapes and %d CPU shape sizes: %s',
                num_gpu_shapes, num_cpu_shapes,
                ', '.join(s.shape for s in shapes if s.is_gpu))
    if not shapes or (not args.use_sdk and num_gpu_shapes == 0):
        raise RuntimeError('No shapes were priced; the estimator or price '
                           'list format may have changed. Refusing to write '
                           'an empty catalog.')

    availability: Availability
    if args.use_sdk:
        availability = sdk_availability(args.profile, args.regions)
        if not any(shape.is_gpu for shape in shapes
                   for zones in availability.values()
                   for visible in zones.values()
                   if visible is not None and shape.shape in visible):
            logger.warning(
                'The tenancy is not entitled to any GPU shape in its '
                'subscribed regions; the catalog will contain CPU shapes '
                'only. Run without --use-sdk for the full price list.')
    else:
        regions = REGIONS
        if args.regions:
            regions = {}
            for region in args.regions:
                if region not in REGIONS:
                    logger.warning(
                        '%s is not a known commercial region; assuming one '
                        'availability domain.', region)
                regions[region] = REGIONS.get(region, 1)
        availability = {
            region: {zone: None for zone in zones
                    } for region, zones in static_availability(regions).items()
        }

    rows = expand_rows(shapes, availability)
    if not rows:
        raise RuntimeError('No catalog rows produced; refusing to overwrite '
                           f'{args.output}.')
    write_csv(rows, args.output)
    logger.info('Wrote %d rows for %d regions to %s', len(rows),
                len(availability), args.output)


if __name__ == '__main__':
    main()
