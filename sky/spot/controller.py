"""The controller module handles the life cycle of a sky spot cluster (job)."""

import argparse
import pathlib
import time
from typing import Optional

import filelock

import sky
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.skylet import job_lib
from sky.spot import recovery_strategy
from sky.spot import spot_state
from sky.spot import spot_utils

logger = sky_logging.init_logger(__name__)


class SpotController:
    """Each spot controller manages the life cycle of one spot cluster (job)."""

    def __init__(self, job_id: int, task_yaml: str) -> None:
        self._job_id = job_id
        self._task_name = pathlib.Path(task_yaml).stem
        self._task = sky.Task.from_yaml(task_yaml)
        # TODO(zhwu): this assumes the specific backend.
        self.backend = cloud_vm_ray_backend.CloudVmRayBackend()

        spot_state.init(self._job_id,
                        self._task_name,
                        self.backend.run_timestamp,
                        resources_str=backend_utils.get_task_resources_str(
                            self._task))
        self._cluster_name = spot_utils.generate_spot_cluster_name(
            self._task_name, self._job_id)
        self._strategy_executor = recovery_strategy.StrategyExecutor.make(
            self._cluster_name, self.backend, self._task)

    def _run(self):
        """Busy loop monitoring spot cluster status and handling recovery."""
        logger.info(f'Started monitoring spot task {self._task_name} '
                    f'(id: {self._job_id})')
        spot_state.set_starting(self._job_id)
        self._strategy_executor.launch()
        spot_state.set_started(self._job_id)
        while True:
            time.sleep(spot_utils.JOB_STATUS_CHECK_GAP_SECONDS)
            # Handle the signal if it is sent by the user.
            user_signal = self._check_signal()
            if user_signal == spot_utils.UserSignal.CANCEL:
                logger.info(f'User sent {user_signal.value} signal.')
                spot_state.set_cancelled(self._job_id)
                break

            # Check the network connection to avoid false alarm for job failure.
            # Network glitch was observed even in the VM.
            try:
                backend_utils.check_network_connection()
            except exceptions.NetworkError:
                logger.info(
                    'Network is not available. Retrying again in '
                    f'{spot_utils.JOB_STATUS_CHECK_GAP_SECONDS} seconds.')
                continue

            # NOTE: we do not check cluster status first because race condition
            # can occur, i.e. cluster can be down during the job status check.
            job_status = self._job_status_check()

            if job_status is not None and not job_status.is_terminal():
                # The job is healthy, continue to monitor the job status.
                continue

            if job_status == job_lib.JobStatus.SUCCEEDED:
                # The job is done.
                spot_state.set_succeeded(self._job_id)
                break

            assert (job_status is None or
                    job_status == job_lib.JobStatus.FAILED), (
                        f'The job should not be {job_status.value}.')
            if job_status == job_lib.JobStatus.FAILED:
                # Check the status of the spot cluster. It can be STOPPED or UP,
                # where STOPPED means partially down.
                cluster_status = backend_utils.refresh_cluster_status_handle(
                    self._cluster_name, force_refresh=True)[0]
                if cluster_status == global_user_state.ClusterStatus.UP:
                    # The user code has probably crashed.
                    spot_state.set_failed(self._job_id)
                    break
                assert (cluster_status == global_user_state.ClusterStatus.
                        STOPPED), ('The cluster should be STOPPED, but is '
                                   f'{cluster_status.value}.')
            # Failed to connect to the cluster or the cluster is partially down.
            # job_status is None or job_status == job_lib.JobStatus.FAILED
            logger.info('The cluster is preempted.')
            spot_state.set_recovering(self._job_id)
            self._strategy_executor.recover()
            spot_state.set_recovered(self._job_id)

    def start(self):
        """Start the controller."""
        try:
            self._run()
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            logger.error(f'Unexpected error occured: {type(e).__name__}: {e}')
        finally:
            self._strategy_executor.terminate_cluster()
            job_status = spot_state.get_status(self._job_id)
            # The job can be non-terminal if the controller exited abnormally,
            # e.g. failed to launch cluster after reaching the MAX_RETRY.
            if not job_status.is_terminal():
                spot_state.set_failed(self._job_id)

    def _job_status_check(self) -> Optional['job_lib.JobStatus']:
        """Check the status of the job running on the spot cluster.

        It can be None, RUNNING, SUCCEEDED, FAILED or CANCELLED.
        """
        handle = global_user_state.get_handle_from_cluster_name(
            self._cluster_name)
        status = None
        try:
            logger.info('=== Checking the job status... ===')
            status = self.backend.get_job_status(handle, stream_logs=False)
            logger.info(f'Job status: {status}')
        except SystemExit:
            # Fail to connect to the cluster
            logger.info('Fail to connect to the cluster.')
        assert status != job_lib.JobStatus.INIT, (
            'Job status should not be INIT')
        logger.info('=' * 34)
        return status

    def _check_signal(self) -> Optional[spot_utils.UserSignal]:
        """Check if the user has sent down signal."""
        signal_file = pathlib.Path(
            spot_utils.SIGNAL_FILE_PREFIX.format(self._job_id))
        signal = None
        if signal_file.exists():
            # Filelock is needed to prevent race condition with concurrent
            # signal writing.
            # TODO(mraheja): remove pylint disabling when filelock version
            # updated
            # pylint: disable=abstract-class-instantiated
            with filelock.FileLock(str(signal_file) + '.lock'):
                with signal_file.open(mode='r') as f:
                    signal = f.read().strip()
                    signal = spot_utils.UserSignal(signal)
                # Remove the signal file, after reading the signal.
                signal_file.unlink()
        return signal


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id',
                        required=True,
                        type=int,
                        help='Job id for the controller job.')
    parser.add_argument('task_yaml',
                        type=str,
                        help='The path to the user spot task yaml file. '
                        'The file name is the spot task name.')
    args = parser.parse_args()
    controller = SpotController(args.job_id, args.task_yaml)
    controller.start()
