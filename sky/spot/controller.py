"""The controller module handles the life cycle of a sky spot cluster (job)."""

import argparse
import enum
import pathlib
import time
from typing import Optional

import sky
from sky import global_user_state
from sky import sky_logging
from sky.spot import spot_status
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.skylet import job_lib
from sky.spot.recovery_strategy import Strategy

logger = sky_logging.init_logger(__name__)

_JOB_STATUS_CHECK_GAP_SECONDS = 60
_SIGNAL_PREFIX = '/tmp/sky_spot_controller_singal_{}'


class UserSignal(enum.Enum):
    """The signal to be sent to the user."""
    DOWN = 'DOWN'
    # TODO(zhwu): We can have more communication signals here if needed
    # in the future.


class SpotController:
    """Each spot controller manages the life cycle of one spot cluster (job)."""

    def __init__(self, task_yaml: str) -> None:
        self.task_name = pathlib.Path(task_yaml).stem
        self.task = sky.Task.from_yaml(task_yaml)
        # TODO(zhwu): this assumes the specific backend.
        self.backend = cloud_vm_ray_backend.CloudVmRayBackend()

        self.job_id = spot_status.submit(
            self.task_name,
            self.backend.run_timestamp,
            resources_str=backend_utils.get_task_resources_str(self.task))
        self.cluster_name = f'{self.task_name}-{self.job_id}'
        self.strategy = Strategy.from_task(self.cluster_name, self.backend,
                                           self.task)

    def _run(self):
        """Busy loop monitoring spot cluster status and handling recovery."""
        logger.info(
            f'Start monitoring spot task {self.task_name} (id: {self.job_id})')
        spot_status.starting(self.job_id)
        self.strategy.launch()
        spot_status.started(self.job_id)
        while True:
            user_signal = self._check_signal()
            if user_signal == UserSignal.DOWN:
                logger.info('User sent down signal.')
                spot_status.cancelled(self.job_id)
                break

            # NOTE: we do not check cluster status first because race condition
            # can occur, i.e. cluster can be down during the job status check.
            # Refer to the design doc's Spot Controller Workflow section
            # https://docs.google.com/document/d/1vt6yGIK6wFYMkHC9HVTe_oISxPR90ugCliMXZKu762E/edit?usp=sharing # pylint: disable=line-too-long
            job_status = self._job_status_check()
            assert job_status != job_lib.JobStatus.INIT, (
                'Job status should not INIT')
            if job_status is not None and not job_status.is_terminal():
                # The job is normally running, continue to monitor the job status.
                time.sleep(_JOB_STATUS_CHECK_GAP_SECONDS)
                continue

            if job_status == job_lib.JobStatus.SUCCEEDED:
                # The job is done.
                spot_status.succeeded(self.job_id)
                break

            assert job_status is None or job_status == job_lib.JobStatus.FAILED, (
                f'The job should not be {job_status.value}.')
            if job_status == job_lib.JobStatus.FAILED:
                # Check the status of the spot cluster. It can be STOPPED or UP,
                # where STOPPED means partially down.
                cluster_status = backend_utils.get_cluster_status_with_refresh(
                    self.cluster_name, force_refresh=True)
                if cluster_status == global_user_state.ClusterStatus.UP:
                    # The user code has probably crashed.
                    spot_status.failed(self.job_id)
                    break
                assert cluster_status == global_user_state.ClusterStatus.STOPPED, (
                    f'The cluster should be STOPPED, but is {cluster_status.value}.'
                )
            # Failed to connect to the cluster or the cluster is partially down.
            # job_status is None or job_status == job_lib.JobStatus.FAILED
            logger.info('The cluster is Preempted.')
            spot_status.recovering(self.job_id)
            self.strategy.recover()
            spot_status.recovered(self.job_id)

    def start(self):
        """Start the controller."""
        try:
            self._run()
        finally:
            self.strategy.terminate()
            status = spot_status.get_status(self.job_id)
            if not status.is_terminal():
                spot_status.failed(self.job_id)

    def _job_status_check(self) -> Optional['job_lib.JobStatus']:
        """Check the status of the job running on the spot cluster.
        It can be INIT, RUNNING, SUCCEEDED, FAILED or CANCELLED."""
        handle = global_user_state.get_handle_from_cluster_name(
            self.cluster_name)
        status = None
        try:
            logger.info('=== Checking the job status... ===')
            status = self.backend.get_job_status(handle, stream_logs=False)
            logger.info(f'Job status: {status}')
        except SystemExit:
            # Fail to connect to the cluster
            logger.info('Fail to connect to the cluster.')
        logger.info('=' * 34)
        return status

    def _check_signal(self) -> UserSignal:
        """Check if the user has sent down signal."""
        singal_file = pathlib.Path(_SIGNAL_PREFIX.format(self.job_id))
        signal = None
        if singal_file.exists():
            with open(singal_file, 'r') as f:
                signal = f.read().strip()
                signal = UserSignal(signal)
            # Remove the signal file, after reading the signal.
            singal_file.unlink()
        return signal


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('task_yaml',
                        type=str,
                        help='The path to the user spot task yaml file. '
                        'The file name is the spot task name.')
    args = parser.parse_args()
    controller = SpotController(args.task_yaml)
    controller.start()
