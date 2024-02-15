"""Classes for replica properties and statuses"""
import dataclasses
import enum
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from sky import backends
from sky import global_user_state
from sky import sky_logging
from sky.serve import constants as serve_constants
from sky.serve import serve_state
from sky.utils import common_utils

logger = sky_logging.init_logger(__name__)


class ProcessStatus(enum.Enum):
    """Process status."""

    # The process is running
    RUNNING = 'RUNNING'

    # The process is finished and succeeded
    SUCCEEDED = 'SUCCEEDED'

    # The process is interrupted
    INTERRUPTED = 'INTERRUPTED'

    # The process failed
    FAILED = 'FAILED'


@dataclasses.dataclass
class ReplicaStatusProperty:
    """Some properties that determine replica status.

    Attributes:
        sky_launch_status: Process status of sky.launch.
        user_app_failed: Whether the service job failed.
        service_ready_now: Latest readiness probe result.
        first_ready_time: The first time the service is ready.
        sky_down_status: Process status of sky.down.
    """
    # None means sky.launch is not called yet.
    sky_launch_status: Optional[ProcessStatus] = None
    user_app_failed: bool = False
    service_ready_now: bool = False
    # None means readiness probe is not passed yet.
    first_ready_time: Optional[float] = None
    # None means sky.down is not called yet.
    sky_down_status: Optional[ProcessStatus] = None
    # The replica's spot instance was preempted.
    preempted: bool = False

    def is_scale_down_succeeded(self, initial_delay_seconds: int) -> bool:
        """Whether to remove the replica record from the replica table.

        If not, the replica will stay in the replica table permanently to
        notify the user that something is wrong with the user code / setup.
        """
        if self.sky_launch_status == ProcessStatus.INTERRUPTED:
            return True
        if self.sky_launch_status != ProcessStatus.SUCCEEDED:
            return False
        if self.sky_down_status != ProcessStatus.SUCCEEDED:
            return False
        if (self.first_ready_time is not None and
                time.time() - self.first_ready_time > initial_delay_seconds):
            # If the service is up for more than `initial_delay_seconds`,
            # we assume there is no bug in the user code and the scale down
            # is successful, thus enabling the controller to remove the
            # replica from the replica table and auto restart the replica.
            # Here we assume that initial_delay_seconds is larger than
            # consecutive_failure_threshold_seconds, so if a replica is not
            # teardown for initial_delay_seconds, it is safe to assume that
            # it is UP for initial_delay_seconds.
            # For replica with a failed sky.launch, it is likely due to some
            # misconfigured resources, so we don't want to auto restart it.
            # For replica with a failed sky.down, we cannot restart it since
            # otherwise we will have a resource leak.
            return True
        if self.user_app_failed:
            return False
        if self.preempted:
            return True
        if not self.service_ready_now:
            return False
        return self.first_ready_time is not None

    def should_track_service_status(self) -> bool:
        """Should we track the status of the replica.

        This includes:
            (1) Job status;
            (2) Readiness probe.
        """
        if self.sky_launch_status != ProcessStatus.SUCCEEDED:
            return False
        if self.sky_down_status is not None:
            return False
        if self.user_app_failed:
            return False
        if self.preempted:
            return False
        return True

    def to_replica_status(self) -> serve_state.ReplicaStatus:
        """Convert status property to human-readable replica status."""
        if self.sky_launch_status is None:
            # Pending to launch
            return serve_state.ReplicaStatus.PENDING
        if self.sky_launch_status == ProcessStatus.RUNNING:
            # Still launching
            return serve_state.ReplicaStatus.PROVISIONING
        if self.sky_down_status is not None:
            if self.preempted:
                # Replica (spot) is preempted
                return serve_state.ReplicaStatus.PREEMPTED
            if self.sky_down_status == ProcessStatus.RUNNING:
                # sky.down is running
                return serve_state.ReplicaStatus.SHUTTING_DOWN
            if self.sky_launch_status == ProcessStatus.INTERRUPTED:
                return serve_state.ReplicaStatus.SHUTTING_DOWN
            if self.sky_down_status == ProcessStatus.FAILED:
                # sky.down failed
                return serve_state.ReplicaStatus.FAILED_CLEANUP
            if self.user_app_failed:
                # Failed on user setup/run
                return serve_state.ReplicaStatus.FAILED
            if self.first_ready_time is None:
                # initial delay seconds exceeded
                return serve_state.ReplicaStatus.FAILED
            if not self.service_ready_now:
                # Max continuous failure exceeded
                return serve_state.ReplicaStatus.FAILED
            if self.sky_launch_status == ProcessStatus.FAILED:
                # sky.launch failed
                return serve_state.ReplicaStatus.FAILED
            # This indicate it is a scale_down with correct teardown.
            # Should have been cleaned from the replica table.
            return serve_state.ReplicaStatus.UNKNOWN
        if self.sky_launch_status == ProcessStatus.FAILED:
            # sky.launch failed
            # The down process has not been started if it reaches here,
            # due to the `if self.sky_down_status is not None`` check above.
            # However, it should have been started by _refresh_process_pool.
            # If not started, this means some bug prevent sky.down from
            # executing. It is also a potential resource leak, so we mark
            # it as FAILED_CLEANUP.
            return serve_state.ReplicaStatus.FAILED_CLEANUP
        if self.user_app_failed:
            # Failed on user setup/run
            # Same as above, the down process should have been started.
            return serve_state.ReplicaStatus.FAILED_CLEANUP
        if self.service_ready_now:
            # Service is ready
            return serve_state.ReplicaStatus.READY
        if self.first_ready_time is not None:
            # Service was ready before but not now
            return serve_state.ReplicaStatus.NOT_READY
        else:
            # No readiness probe passed and sky.launch finished
            return serve_state.ReplicaStatus.STARTING


class ReplicaInfo:
    """Replica info for each replica."""

    def __init__(self, replica_id: int, cluster_name: str, replica_port: str,
                 version: int) -> None:
        self.replica_id: int = replica_id
        self.cluster_name: str = cluster_name
        self.version: int = version
        self.replica_port: str = replica_port
        self.first_not_ready_time: Optional[float] = None
        self.consecutive_failure_times: List[float] = []
        self.status_property: ReplicaStatusProperty = ReplicaStatusProperty()

    def handle(
        self,
        cluster_record: Optional[Dict[str, Any]] = None
    ) -> Optional[backends.CloudVmRayResourceHandle]:
        """Get the handle of the cluster.

        Args:
            cluster_record: The cluster record in the cluster table. If not
                provided, will fetch the cluster record from the cluster table
                based on the cluster name.
        """
        if cluster_record is None:
            cluster_record = global_user_state.get_cluster_from_name(
                self.cluster_name)
        if cluster_record is None:
            return None
        handle = cluster_record['handle']
        assert isinstance(handle, backends.CloudVmRayResourceHandle)
        return handle

    @property
    def is_launched(self) -> bool:
        return self.status in serve_state.ReplicaStatus.launched_statuses()

    @property
    def is_ready(self) -> bool:
        return self.status == serve_state.ReplicaStatus.READY

    @property
    def url(self) -> Optional[str]:
        handle = self.handle()
        if handle is None:
            return None
        return f'{handle.head_ip}:{self.replica_port}'

    @property
    def status(self) -> serve_state.ReplicaStatus:
        replica_status = self.status_property.to_replica_status()
        if replica_status == serve_state.ReplicaStatus.UNKNOWN:
            logger.error('Detecting UNKNOWN replica status for '
                         f'replica {self.replica_id}.')
        return replica_status

    def to_info_dict(self, with_handle: bool) -> Dict[str, Any]:
        cluster_record = global_user_state.get_cluster_from_name(
            self.cluster_name)
        info_dict = {
            'replica_id': self.replica_id,
            'name': self.cluster_name,
            'status': self.status,
            'version': self.version,
            'launched_at': (cluster_record['launched_at']
                            if cluster_record is not None else None),
        }
        if with_handle:
            info_dict['handle'] = self.handle(cluster_record)
        return info_dict

    def probe(
        self,
        readiness_path: str,
        post_data: Optional[Dict[str, Any]],
    ) -> Tuple['ReplicaInfo', bool, float]:
        """Probe the readiness of the replica.

        Returns:
            Tuple of (self, is_ready, probe_time).
        """
        replica_identity = f'replica {self.replica_id} with url {self.url}'
        # TODO(tian): This requiring the clock on each replica to be aligned,
        # which may not be true when the GCP VMs have run for a long time. We
        # should have a better way to do this. See #2539 for more information.
        probe_time = time.time()
        try:
            msg = ''
            # TODO(tian): Support HTTPS in the future.
            readiness_path = (f'http://{self.url}{readiness_path}')
            if post_data is not None:
                msg += 'POST'
                response = requests.post(
                    readiness_path,
                    json=post_data,
                    timeout=serve_constants.READINESS_PROBE_TIMEOUT_SECONDS)
            else:
                msg += 'GET'
                response = requests.get(
                    readiness_path,
                    timeout=serve_constants.READINESS_PROBE_TIMEOUT_SECONDS)
            msg += (f' request to {replica_identity} returned status '
                    f'code {response.status_code}')
            if response.status_code == 200:
                msg += '.'
            else:
                msg += f' and response {response.text}.'
            logger.info(msg)
            if response.status_code == 200:
                logger.debug(f'{replica_identity.capitalize()} is ready.')
                return self, True, probe_time
        except requests.exceptions.RequestException as e:
            logger.info(f'Error when probing {replica_identity}: '
                        f'{common_utils.format_exception(e)}.')
        return self, False, probe_time
