"""Utility functions for resources."""
import dataclasses
import enum
import itertools
import json
import math
import re
import typing
from typing import Dict, List, Optional, Set, Union

from sky import skypilot_config
from sky.utils import registry
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import backends
    from sky import resources as resources_lib

_PORT_RANGE_HINT_MSG = ('Invalid port range {}. Please use the format '
                        '"from-to", in which from <= to. e.g. "1-3".')
_PORT_HINT_MSG = ('Invalid port {}. '
                  'Please use a port number between 1 and 65535.')
_DEFAULT_MESSAGE_HANDLE_INITIALIZING = '<initializing>'


class DiskTier(enum.Enum):
    """All disk tiers supported by SkyPilot."""
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    ULTRA = 'ultra'
    BEST = 'best'

    @classmethod
    def supported_tiers(cls) -> List[str]:
        # 'none' is a special tier for overriding
        # the tier specified in task YAML.
        return [tier.value for tier in cls] + ['none']

    @classmethod
    def cli_help_message(cls) -> str:
        return (
            f'OS disk tier. Could be one of {", ".join(cls.supported_tiers())}'
            f'. If {cls.BEST.value} is specified, use the best possible disk '
            'tier. If none is specified, enforce to use default value and '
            f'override the option in task YAML. Default: {cls.MEDIUM.value}')

    def __le__(self, other: 'DiskTier') -> bool:
        types = list(DiskTier)
        return types.index(self) <= types.index(other)


@dataclasses.dataclass
class ClusterName:
    display_name: str
    name_on_cloud: str

    def __repr__(self) -> str:
        return repr(self.display_name)

    def __str__(self) -> str:
        return self.display_name


def check_port_str(port: str) -> None:
    if not port.isdigit():
        with ux_utils.print_exception_no_traceback():
            raise ValueError(_PORT_HINT_MSG.format(port))
    if not 1 <= int(port) <= 65535:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(_PORT_HINT_MSG.format(port))


def check_port_range_str(port_range: str) -> None:
    range_list = port_range.split('-')
    if len(range_list) != 2:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(_PORT_RANGE_HINT_MSG.format(port_range))
    from_port, to_port = range_list
    check_port_str(from_port)
    check_port_str(to_port)
    if int(from_port) > int(to_port):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(_PORT_RANGE_HINT_MSG.format(port_range))


def port_ranges_to_set(ports: Optional[List[str]]) -> Set[int]:
    """Parse a list of port ranges into a set that containing no duplicates.

    For example, ['1-3', '5-7'] will be parsed to {1, 2, 3, 5, 6, 7}.
    """
    if ports is None:
        return set()
    port_set = set()
    for port in ports:
        if port.isdigit():
            check_port_str(port)
            port_set.add(int(port))
        else:
            check_port_range_str(port)
            from_port, to_port = port.split('-')
            port_set.update(range(int(from_port), int(to_port) + 1))
    return port_set


def port_set_to_ranges(port_set: Optional[Set[int]]) -> List[str]:
    """Parse a set of ports into the skypilot ports format.

    This function will group consecutive ports together into a range,
    and keep the rest as is. For example, {1, 2, 3, 5, 6, 7} will be
    parsed to ['1-3', '5-7'].
    """
    if port_set is None:
        return []
    ports: List[str] = []
    # Group consecutive ports together.
    # This algorithm is based on one observation: consecutive numbers
    # in a sorted list will have the same difference with their indices.
    # For example, in [1, 2, 3, 5, 6, 7], difference between value and index
    # is [1, 1, 1, 2, 2, 2], and the consecutive numbers are [1, 2, 3] and
    # [5, 6, 7].
    for _, group in itertools.groupby(enumerate(sorted(port_set)),
                                      lambda x: x[1] - x[0]):
        port = [g[1] for g in group]
        if len(port) == 1:
            ports.append(str(port[0]))
        else:
            ports.append(f'{port[0]}-{port[-1]}')
    return ports


def simplify_ports(ports: List[str]) -> List[str]:
    """Simplify a list of ports.

    For example, ['1-2', '3', '5-6', '7'] will be simplified to ['1-3', '5-7'].
    """
    return port_set_to_ranges(port_ranges_to_set(ports))


def format_resource(resource: 'resources_lib.Resources',
                    simplify: bool = False) -> str:
    if simplify:
        cloud = resource.cloud
        if resource.accelerators is None:
            vcpu, _ = cloud.get_vcpus_mem_from_instance_type(
                resource.instance_type)
            hardware = f'vCPU={int(vcpu)}'
        else:
            hardware = f'{resource.accelerators}'
        spot = '[Spot]' if resource.use_spot else ''
        return f'{cloud}({spot}{hardware})'
    else:
        # accelerator_args is way too long.
        # Convert from:
        #  GCP(n1-highmem-8, {'tpu-v2-8': 1}, accelerator_args={'runtime_version': '2.12.0'}  # pylint: disable=line-too-long
        # to:
        #  GCP(n1-highmem-8, {'tpu-v2-8': 1}...)
        pattern = ', accelerator_args={.*}'
        launched_resource_str = re.sub(pattern, '...', str(resource))
        return launched_resource_str


def get_readable_resources_repr(handle: 'backends.CloudVmRayResourceHandle',
                                simplify: bool = False) -> str:
    if (handle.launched_nodes is not None and
            handle.launched_resources is not None):
        return (f'{handle.launched_nodes}x '
                f'{format_resource(handle.launched_resources, simplify)}')
    return _DEFAULT_MESSAGE_HANDLE_INITIALIZING


def make_ray_custom_resources_str(
        resource_dict: Optional[Dict[str, Union[int, float]]]) -> Optional[str]:
    """Convert resources to Ray custom resources format."""
    if resource_dict is None:
        return None
    # Ray does not allow fractional resources, so we need to ceil the values.
    ceiled_dict = {k: math.ceil(v) for k, v in resource_dict.items()}
    return json.dumps(ceiled_dict, separators=(',', ':'))


@dataclasses.dataclass
class FeasibleResources:
    """Feasible resources returned by cloud.

    Used to represent a collection of feasible resources returned by cloud,
    any fuzzy candidates, and optionally a string hint if no feasible resources
    are found.

    Fuzzy candidates example: when the requested GPU is A100:1 but is not
    available in a cloud/region, the fuzzy candidates are results of a fuzzy
    search in the catalog that are offered in the location. E.g.,
    ['A100-80GB:1', 'A100-80GB:2', 'A100-80GB:4', 'A100:8']
    """
    resources_list: List['resources_lib.Resources']
    fuzzy_candidate_list: List[str]
    hint: Optional[str]


def need_to_query_reservations() -> bool:
    """Checks if we need to query reservations from cloud APIs.

    We need to query reservations if:
    - The cloud has specific reservations.
    - The cloud prioritizes reservations over on-demand instances.

    This is useful to skip the potentially expensive reservation query for
    clouds that do not use reservations.
    """
    for cloud_str in registry.CLOUD_REGISTRY.keys():
        cloud_specific_reservations = skypilot_config.get_nested(
            (cloud_str, 'specific_reservations'), None)
        cloud_prioritize_reservations = skypilot_config.get_nested(
            (cloud_str, 'prioritize_reservations'), False)
        if (cloud_specific_reservations is not None or
                cloud_prioritize_reservations):
            return True
    return False


def make_launchables_for_valid_region_zones(
    launchable_resources: 'resources_lib.Resources',
    override_optimize_by_zone: bool = False,
) -> List['resources_lib.Resources']:
    assert launchable_resources.is_launchable()
    # In principle, all provisioning requests should be made at the granularity
    # of a single zone. However, for on-demand instances, we batch the requests
    # to the zones in the same region in order to leverage the region-level
    # provisioning APIs of AWS and Azure. This way, we can reduce the number of
    # API calls, and thus the overall failover time. Note that this optimization
    # does not affect the user cost since the clouds charge the same prices for
    # on-demand instances in the same region regardless of the zones. On the
    # other hand, for spot instances, we do not batch the requests because the
    # "AWS" spot prices may vary across zones.
    # For GCP, we do not batch the requests because GCP reservation system is
    # zone based. Therefore, price estimation is potentially different across
    # zones.

    # NOTE(woosuk): GCP does not support region-level provisioning APIs. Thus,
    # while we return per-region resources here, the provisioner will still
    # issue the request for one zone at a time.
    # NOTE(woosuk): If we support Azure spot instances, we should batch the
    # requests since Azure spot prices are region-level.
    # TODO(woosuk): Batch the per-zone AWS spot instance requests if they are
    # in the same region and have the same price.
    # TODO(woosuk): A better design is to implement batching at a higher level
    # (e.g., in provisioner or optimizer), not here.
    launchables = []
    regions = launchable_resources.get_valid_regions_for_launchable()
    for region in regions:
        optimize_by_zone = (override_optimize_by_zone or
                            launchable_resources.cloud.optimize_by_zone())
        # It is possible that we force the optimize_by_zone but some clouds
        # do not support zone-level provisioning (i.e. Azure). So we check
        # if there is zone-level information in the region first.
        if (region.zones is not None and
            (launchable_resources.use_spot or optimize_by_zone)):
            # Spot instances.
            # Do not batch the per-zone requests.
            for zone in region.zones:
                launchables.append(
                    launchable_resources.copy(region=region.name,
                                              zone=zone.name))
        else:
            # On-demand instances.
            # Batch the requests at the granularity of a single region.
            launchables.append(launchable_resources.copy(region=region.name))
    return launchables
