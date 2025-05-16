"""Utility functions for handling infrastructure specifications."""
import dataclasses
from typing import Optional

from sky.utils import ux_utils


@dataclasses.dataclass
class InfraInfo:
    """Infrastructure information parsed from infra string.
    
    When a field is None, it means the field is not specified.
    """
    cloud: Optional[str] = None
    region: Optional[str] = None
    zone: Optional[str] = None

    @staticmethod
    def from_str(infra: str) -> 'InfraInfo':
        """Parse the infra string into cloud, region, and zone components.
        
        The format of the infra string is `cloud`, `cloud/region`, or
        `cloud/region/zone`. Examples: `aws`, `aws/us-east-1`,
        `aws/us-east-1/us-east-1a`. For any field, you can use `*` to indicate
        that any value is acceptable.

        If `*` is used for any field, the InfraInfo will have None for that field.

        Args:
            infra: A string in the format of `cloud`, `cloud/region`, or
                `cloud/region/zone`. Examples: `aws`, `aws/us-east-1`,
                `aws/us-east-1/us-east-1a`.
        
        Returns:
            An InfraInfo object containing cloud, region, and zone information.
        
        Raises:
            ValueError: If the infra string is malformed.
        """
        parts = infra.strip().split('/')  # Split only on the first / to get cloud

        if not parts:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Invalid infra format: {infra}. Expected format is '
                    f'"cloud/region" or "cloud/region/zone".')

        cloud_name = parts[0].lower()

        # Handle Kubernetes contexts specially, as they can contain slashes
        if cloud_name in ['k8s', 'kubernetes']:
            # For Kubernetes, the entire string after "k8s/" is the context name (region)
            region = parts[1] if len(parts) >= 2 else None
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
        if cloud_name == '*':
            cloud_name = None
        if region == '*':
            region = None
        if zone == '*':
            zone = None
        return InfraInfo(cloud=cloud_name, region=region, zone=zone)


def format_infra(cloud: Optional[str],
                 region: Optional[str] = None,
                 zone: Optional[str] = None) -> Optional[str]:
    """Formats cloud, region, and zone into an infra string.
    
    Args:
        cloud: The cloud object
        region: The region name
        zone: The zone name
        
    Returns:
        A formatted infra string, or None if cloud is None
    """
    if cloud is None:
        cloud = '*'
    if region is None:
        region = '*'
    if zone is None:
        zone = '*'

    # Build the parts list and filter out trailing wildcards
    parts = [str(cloud), region, zone]
    while parts and parts[-1] == '*':
        parts.pop()

    # If no parts remain, return None
    if not parts:
        return None

    # Join the parts with '/'
    return '/'.join(parts)
