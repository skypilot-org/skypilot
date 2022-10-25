"""Controller: handles the life cycle of a managed spot cluster (job)."""
import argparse
import pathlib
import time
import traceback

import colorama
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

    def __init__(self, job_id: int, task_yaml: str,
                 retry_until_up: bool) -> None:
        self._job_id = job_id
        self._task_name = pathlib.Path(task_yaml).stem
        self._task = sky.Task.from_yaml(task_yaml)

        self._retry_until_up = retry_until_up
        # TODO(zhwu): this assumes the specific backend.
        self.backend = cloud_vm_ray_backend.CloudVmRayBackend()

        # Add a unique identifier to the task environment variables, so that
        # the user can have the same id for multiple recoveries.
        #   Example value: sky-2022-10-04-22-46-52-467694_id-17
        # TODO(zhwu): support SKYPILOT_RUN_ID for normal jobs as well, so
        # the use can use env_var for normal jobs.
        task_envs = self._task.envs or {}
        task_envs['SKYPILOT_RUN_ID'] = (f'{self.backend.run_timestamp}'
                                        f'_id-{self._job_id}')
        self._task.set_envs(task_envs)

        spot_state.set_submitted(
            self._job_id,
            self._task_name,
            self.backend.run_timestamp,
            resources_str=backend_utils.get_task_resources_str(self._task))
        self._cluster_name = spot_utils.generate_spot_cluster_name(
            self._task_name, self._job_id)
        self._strategy_executor = recovery_strategy.StrategyExecutor.make(
            self._cluster_name, self.backend, self._task, retry_until_up,
            self._handle_signal)

    def _run(self):
        """Busy loop monitoring spot cluster status and handling recovery."""
        logger.info(f'Started monitoring spot task {self._task_name} '
                    f'(id: {self._job_id})')
        spot_state.set_starting(self._job_id)
        start_at = self._strategy_executor.launch()

        spot_state.set_started(self._job_id, start_time=start_at)
        while True:
            time.sleep(spot_utils.JOB_STATUS_CHECK_GAP_SECONDS)
            # Handle the signal if it is sent by the user.
            self._handle_signal()

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
            job_status = spot_utils.get_job_status(self.backend,
                                                   self._cluster_name)

            if job_status is not None and not job_status.is_terminal():
                # The job is healthy, continue to monitor the job status.
                continue

            if job_status == job_lib.JobStatus.SUCCEEDED:
                end_time = spot_utils.get_job_timestamp(self.backend,
                                                        self._cluster_name,
                                                        get_end_time=True)
                # The job is done.
                spot_state.set_succeeded(self._job_id, end_time=end_time)
                break

            assert (job_status is None or
                    job_status == job_lib.JobStatus.FAILED), (
                        f'The job should not be {job_status.value}.')
            if job_status == job_lib.JobStatus.FAILED:
                # Check the status of the spot cluster. It can be STOPPED or UP,
                # where STOPPED means partially down.
                (cluster_status,
                 handle) = backend_utils.refresh_cluster_status_handle(
                     self._cluster_name, force_refresh=True)
                if cluster_status == global_user_state.ClusterStatus.UP:
                    # The user code has probably crashed.
                    end_time = spot_utils.get_job_timestamp(self.backend,
                                                            self._cluster_name,
                                                            get_end_time=True)
                    logger.info(
                        'The user job failed. Please check the logs below.\n'
                        f'== Logs of the user job (ID: {self._job_id}) ==\n')
                    self.backend.tail_logs(handle,
                                           None,
                                           spot_job_id=self._job_id)
                    logger.info(f'\n== End of logs (ID: {self._job_id}) ==')
                    spot_state.set_failed(
                        self._job_id,
                        failure_type=spot_state.SpotStatus.FAILED,
                        end_time=end_time)
                    break
            # cluster can be down, INIT or STOPPED, based on the interruption
            # behavior of the cloud.
            # Failed to connect to the cluster or the cluster is partially down.
            # job_status is None or job_status == job_lib.JobStatus.FAILED
            logger.info('The cluster is preempted.')
            spot_state.set_recovering(self._job_id)
            recovered_time = self._strategy_executor.recover()
            spot_state.set_recovered(self._job_id,
                                     recovered_time=recovered_time)

    def start(self):
        """Start the controller."""
        try:
            self._run()
        except exceptions.SpotUserCancelledError as e:
            logger.info(e)
            spot_state.set_cancelled(self._job_id)
        except exceptions.ResourcesUnavailableError as e:
            logger.error(f'Resources unavailable: {colorama.Fore.RED}{e}'
                         f'{colorama.Style.RESET_ALL}')
            spot_state.set_failed(
                self._job_id,
                failure_type=spot_state.SpotStatus.FAILED_NO_RESOURCE)
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            logger.error(traceback.format_exc())
            logger.error(f'Unexpected error occurred: {type(e).__name__}: {e}')
        finally:
            self._strategy_executor.terminate_cluster()
            job_status = spot_state.get_status(self._job_id)
            # The job can be non-terminal if the controller exited abnormally,
            # e.g. failed to launch cluster after reaching the MAX_RETRY.
            if not job_status.is_terminal():
                spot_state.set_failed(
                    self._job_id,
                    failure_type=spot_state.SpotStatus.FAILED_CONTROLLER)

            # Clean up Storages with persistent=False.
            self.backend.teardown_ephemeral_storage(self._task)

    def _handle_signal(self):
        """Handle the signal if the user sent it."""
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
        if signal is None:
            return
        if signal == spot_utils.UserSignal.CANCEL:
            raise exceptions.SpotUserCancelledError(
                f'User sent {signal.value} signal.')

        raise RuntimeError(f'Unknown SkyPilot signal received: {signal.value}.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id',
                        required=True,
                        type=int,
                        help='Job id for the controller job.')
    parser.add_argument('--retry-until-up',
                        action='store_true',
                        help='Retry until the spot cluster is up.')
    parser.add_argument('task_yaml',
                        type=str,
                        help='The path to the user spot task yaml file. '
                        'The file name is the spot task name.')
    args = parser.parse_args()
    controller = SpotController(args.job_id, args.task_yaml,
                                args.retry_until_up)
    controller.start()
