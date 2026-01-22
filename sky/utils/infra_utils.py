"""Utility functions for handling infrastructure specifications."""
import dataclasses
from typing import Optional

from sky.utils import common_utils
from sky.utils import ux_utils

_REGION_OR_ZONE_TRUNCATION_LENGTH = 25


@dataclasses.dataclass
class InfraInfo:
    """Infrastructure information parsed from infra string.

    When a field is None, it means the field is not specified.
    """
    cloud: Optional[str] = None
    region: Optional[str] = None
    zone: Optional[str] = None

    def __init__(self,
                 cloud: Optional[str] = None,
                 region: Optional[str] = None,
                 zone: Optional[str] = None):
        assert cloud not in ['none', 'None', 'NONE'], 'cloud must be specified'
        if not cloud or cloud == '*':
            cloud = None
        if not region or region == '*':
            region = None
        if not zone or zone == '*':
            zone = None

        self.cloud = cloud
        self.region = region
        self.zone = zone

    @staticmethod
    def from_str(infra: Optional[str]) -> 'InfraInfo':
        """Parse the infra string into cloud, region, and zone components.

        The format of the infra string is `cloud`, `cloud/region`, or
        `cloud/region/zone`. Examples: `aws`, `aws/us-east-1`,
        `aws/us-east-1/us-east-1a`. For any field, you can use `*` to indicate
        that any value is acceptable.

        If `*` is used for any field, the InfraInfo will have None for that
        field.

        Args:
            infra: A string in the format of `cloud`, `cloud/region`, or
                `cloud/region/zone`. Examples: `aws`, `aws/us-east-1`,
                `aws/us-east-1/us-east-1a`.

        Returns:
            An InfraInfo object containing cloud, region, and zone information.

        Raises:
            ValueError: If the infra string is malformed.
        """
        if infra is None or not infra.strip():
            return InfraInfo()

        infra = infra.strip().strip('/')

        # Split on / to get cloud, region, zone
        parts = [p.strip() for p in infra.strip().split('/')]

        if '' in parts:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Invalid infra format: {infra}. Format should not contain '
                    'empty parts (e.g., double slashes "//").')

        if not parts or not parts[0]:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Invalid infra format: {infra}. Expected format is '
                    '"cloud", "cloud/region", or "cloud/region/zone".')

        cloud_name: Optional[str] = parts[0].lower()

        # Handle Kubernetes contexts specially, as they can contain slashes
        if cloud_name in ['k8s', 'kubernetes']:
            # For Kubernetes, the entire string after "k8s/" is the
            # context name (region)
            cloud_name = 'kubernetes'  # Normalize k8s to kubernetes
            region = '/'.join(parts[1:]) if len(parts) >= 2 else None
            zone = None
        elif cloud_name == 'ssh':
            # For SSH, the entire string after "ssh/" is the
            # node pool name. We prepend 'ssh-' for the internal implementation
            # which reuses the context name.
            # TODO(romilb): This is a workaround while we use the global
            # kubeconfig to store the ssh contexts.
            region = '/'.join(parts[1:]) if len(parts) >= 2 else None
            if region:
                region = f'ssh-{region}'
            zone = None
        else:
            # For non-Kubernetes clouds, continue with regular parsing
            # but be careful to only split into max 3 parts
            region_zone_parts = parts[1:]
            region = None
            zone = None
            if region_zone_parts:
                region = region_zone_parts[0]
                if len(region_zone_parts) > 1:
                    zone = region_zone_parts[1]
                if len(region_zone_parts) > 2:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'Invalid infra format: {infra}. Expected format '
                            'is "cloud", "cloud/region", or '
                            '"cloud/region/zone".')

        if cloud_name == '*':
            cloud_name = None
        if region == '*':
            region = None
        if zone == '*':
            zone = None
        return InfraInfo(cloud=cloud_name, region=region, zone=zone)

    def to_str(self) -> Optional[str]:
        """Formats cloud, region, and zone into an infra string.

        Args:
            cloud: The cloud object
            region: The region name
            zone: The zone name

        Returns:
            A formatted infra string, or None if cloud is None or '*'
        """
        cloud = self.cloud
        region = self.region
        zone = self.zone

        if cloud is None:
            cloud = '*'
        if region is None:
            region = '*'
        if zone is None:
            zone = '*'

        # If the cloud is ssh, we remove the ssh- prefix from the region
        # TODO(romilb): This is a workaround while we use the global
        # kubeconfig to store the ssh contexts.
        if region and region.startswith('ssh-'):
            region = region[4:]

        # Build the parts list and filter out trailing wildcards
        parts = [cloud.lower(), region, zone]
        while parts and parts[-1] == '*':
            parts.pop()

        if not parts:
            return None

        # Join the parts with '/'
        return '/'.join(parts)

    def formatted_str(self, truncate: bool = True) -> str:
        """Formats cloud, region, and zone into an infra string.

        Args:
            truncate: Whether to truncate the region or zone

        Returns:
            A formatted infra string, or None if cloud is None or '*'
        """
        if self.cloud is None or self.cloud == '*':
            return '-'

        region_or_zone = None
        # For Slurm, zones = partitions. We want to show the cluster
        # name (region) instead of the partition name (zone), as different
        # Slurm clusters can easily have same partition name.
        is_slurm = self.cloud.lower() == 'slurm'
        if not is_slurm and self.zone is not None and self.zone != '*':
            region_or_zone = self.zone
        elif self.region is not None and self.region != '*':
            # If using region, we remove the ssh- prefix if it exists for SSH
            # Node Pools.
            # TODO(romilb): This is a workaround while we use the global
            # kubeconfig to store the ssh contexts.
            region_or_zone = common_utils.removeprefix(self.region, 'ssh-')

        if region_or_zone is not None and truncate:
            region_or_zone = common_utils.truncate_long_string(
                region_or_zone,
                _REGION_OR_ZONE_TRUNCATION_LENGTH,
                truncate_middle=True)

        formatted_str = f'{self.cloud}'
        if region_or_zone is not None:
            formatted_str += f' ({region_or_zone})'

        return formatted_str
