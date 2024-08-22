"""Utilities for AWS."""
import dataclasses
import enum
import time
from typing import List

import cachetools

from sky import skypilot_config
from sky.adaptors import aws


class ReservationType(str, enum.Enum):
    DEFAULT = 'default'
    BLOCK = 'capacity-block'


@dataclasses.dataclass
class AWSReservation:
    name: str
    instance_type: str
    zone: str
    available_resources: int
    # Whether the reservation is targeted, i.e. can only be consumed when
    # the reservation name is specified.
    targeted: bool
    type: ReservationType


def use_reservations() -> bool:
    prioritize_reservations = skypilot_config.get_nested(
        ('aws', 'prioritize_reservations'), False)
    specific_reservations = skypilot_config.get_nested(
        ('aws', 'specific_reservations'), set())
    return prioritize_reservations or specific_reservations


@cachetools.cached(cache=cachetools.TTLCache(maxsize=100,
                                             ttl=300,
                                             timer=time.time))
def list_reservations_for_instance_type(
    instance_type: str,
    region: str,
) -> List[AWSReservation]:
    if not use_reservations():
        return []
    ec2 = aws.client('ec2', region_name=region)
    response = ec2.describe_capacity_reservations(Filters=[{
        'Name': 'instance-type',
        'Values': [instance_type]
    }, {
        'Name': 'state',
        'Values': ['active']
    }])
    reservations = response['CapacityReservations']
    return [
        AWSReservation(name=r['CapacityReservationId'],
                       instance_type=r['InstanceType'],
                       zone=r['AvailabilityZone'],
                       available_resources=r['AvailableInstanceCount'],
                       targeted=r['InstanceMatchCriteria'] == 'targeted',
                       type=ReservationType(r.get('ReservationType',
                                                  'default')))
        for r in reservations
    ]
