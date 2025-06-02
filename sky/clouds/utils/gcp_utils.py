"""Utility functions for GCP.

The functions that are used to access GCP APIs and TPU VM. We have the
reservation-related functions here, so that the cache of the reservations can be
shared across multiple clouds.GCP() objects.
"""

import copy
import dataclasses
import json
import time
import typing
from typing import List, Optional, Set

import cachetools

from sky import sky_logging
from sky import skypilot_config
from sky.provision.gcp import constants
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.utils import subprocess_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)


def is_tpu(resources: Optional['resources_lib.Resources']) -> bool:
    if resources is None or resources.accelerators is None:
        return False
    acc, _ = list(resources.accelerators.items())[0]
    return acc.startswith('tpu')


def is_tpu_vm(resources: Optional['resources_lib.Resources']) -> bool:
    if not is_tpu(resources):
        return False
    assert (resources is not None and len(resources.accelerators) == 1)
    acc, _ = list(resources.accelerators.items())[0]
    if kubernetes_utils.is_tpu_on_gke(acc):
        return False
    if resources.accelerator_args is None:
        return True
    return resources.accelerator_args.get('tpu_vm', True)


def is_tpu_vm_pod(resources: Optional['resources_lib.Resources']) -> bool:
    if not is_tpu_vm(resources):
        return False
    assert resources is not None
    acc, _ = list(resources.accelerators.items())[0]
    return not acc.endswith('-8')


@dataclasses.dataclass
class SpecificReservation:
    count: int
    in_use_count: int

    @classmethod
    def from_dict(cls, d: dict) -> 'SpecificReservation':
        return cls(count=int(d['count']), in_use_count=int(d['inUseCount']))


class GCPReservation:
    """GCP Reservation object that contains the reservation information."""

    def __init__(self, self_link: str, zone: str,
                 specific_reservation: SpecificReservation,
                 specific_reservation_required: bool) -> None:
        self.self_link = self_link
        self.zone = zone
        self.specific_reservation = specific_reservation
        self.specific_reservation_required = specific_reservation_required

    @classmethod
    def from_dict(cls, d: dict) -> 'GCPReservation':
        return cls(
            self_link=d['selfLink'],
            zone=d['zone'],
            specific_reservation=SpecificReservation.from_dict(
                d['specificReservation']),
            specific_reservation_required=d['specificReservationRequired'],
        )

    @property
    def available_resources(self) -> int:
        """Count resources available that can be used in this reservation."""
        return (self.specific_reservation.count -
                self.specific_reservation.in_use_count)

    def is_consumable(
        self,
        specific_reservations: Set[str],
    ) -> bool:
        """Check if the reservation is consumable.

        Check if the reservation is consumable with the provided specific
        reservation names. This is defined by the Consumption type.
        For more details:
        https://cloud.google.com/compute/docs/instances/reservations-overview#how-reservations-work
        """
        return (not self.specific_reservation_required or
                self.name in specific_reservations)

    @property
    def name(self) -> str:
        """Name derived from reservation self link.

        The naming convention can be found here:
        https://cloud.google.com/compute/docs/instances/reservations-consume#consuming_a_specific_shared_reservation
        """
        parts = self.self_link.split('/')
        return '/'.join(parts[-6:-4] + parts[-2:])


def list_reservations_for_instance_type_in_zone(
    instance_type: str,
    zone: str,
) -> List[GCPReservation]:
    reservations = _list_reservations_for_instance_type(instance_type)
    return [r for r in reservations if r.zone.endswith(f'/{zone}')]


@cachetools.cached(cache=cachetools.TTLCache(maxsize=1,
                                             ttl=300,
                                             timer=time.time))
def _list_reservations_for_instance_type(
    instance_type: str,) -> List[GCPReservation]:
    """List all reservations for the given instance type.

    TODO: We need to incorporate accelerators because the reserved instance
    can be consumed only when the instance_type + GPU type matches, and in
    GCP GPUs except for A100 and L4 do not have their own instance type.
    For example, if we have a specific reservation with n1-highmem-8
    in us-central1-c. `sky launch --gpus V100` will fail.
    """
    prioritize_reservations = skypilot_config.get_nested(
        ('gcp', 'prioritize_reservations'), False)
    specific_reservations = skypilot_config.get_nested(
        ('gcp', 'specific_reservations'), [])
    if not prioritize_reservations and not specific_reservations:
        return []
    logger.debug(f'Querying GCP reservations for instance {instance_type!r}')
    list_reservations_cmd = (
        'gcloud compute reservations list '
        '--filter="specificReservation.instanceProperties.machineType='
        f'{instance_type} AND status=READY" --format="json('
        'specificReservation.count, specificReservation.inUseCount, '
        'specificReservationRequired, selfLink, zone)"')
    returncode, stdout, stderr = subprocess_utils.run_with_retries(
        list_reservations_cmd,
        # 1: means connection aborted (although it shows 22 in the error,
        # but the actual error code is 1)
        # Example: ERROR: gcloud crashed (ConnectionError): ('Connection aborted.', OSError(22, 'Invalid argument')) # pylint: disable=line-too-long
        retry_returncode=[255, 1],
    )
    subprocess_utils.handle_returncode(
        returncode,
        list_reservations_cmd,
        error_msg=
        f'Failed to get list reservations for {instance_type!r}:\n{stderr}',
        stderr=stderr,
        stream_logs=True,
    )
    return [GCPReservation.from_dict(r) for r in json.loads(stdout)]


def get_minimal_compute_permissions() -> List[str]:
    permissions = copy.copy(constants.VM_MINIMAL_PERMISSIONS)
    if skypilot_config.get_nested(('gcp', 'vpc_name'), None) is None:
        # If custom VPC is not specified, permissions to modify network are
        # required to ensure SkyPilot to be able to setup the network, and
        # allow opening ports (e.g., via `resources.ports`).
        permissions += constants.FIREWALL_PERMISSIONS

    if (skypilot_config.get_nested(('gcp', 'prioritize_reservations'), False) or
            skypilot_config.get_nested(('gcp', 'specific_reservations'), [])):
        permissions += constants.RESERVATION_PERMISSIONS

    permissions += constants.GCP_MINIMAL_PERMISSIONS

    return permissions


def get_minimal_storage_permissions() -> List[str]:
    permissions = copy.copy(constants.STORAGE_MINIMAL_PERMISSIONS)

    permissions += constants.GCP_MINIMAL_PERMISSIONS

    return permissions
