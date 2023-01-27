"""Controller: handles the life cycle of a managed spot cluster (job)."""
import argparse
import multiprocessing
import pathlib
import signal
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
from sky.skylet import constants
from sky.skylet import job_lib
from sky.spot import recovery_strategy
from sky.spot import spot_state
from sky.spot import spot_utils
from sky.utils import common_utils
from sky.utils import subprocess_utils

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
        self._backend = cloud_vm_ray_backend.CloudVmRayBackend()

        # Add a unique identifier to the task environment variables, so that
        # the user can have the same id for multiple recoveries.
        #   Example value: sky-2022-10-04-22-46-52-467694_id-17
        task_envs = self._task.envs or {}
        job_id_env_var = common_utils.get_global_job_id(
            self._backend.run_timestamp, 'spot', str(self._job_id))
        task_envs[constants.JOB_ID_ENV_VAR] = job_id_env_var
        self._task.set_envs(task_envs)

        spot_state.set_submitted(
            self._job_id,
            self._task_name,
            self._backend.run_timestamp,
            resources_str=backend_utils.get_task_resources_str(self._task))
        logger.info(f'Submitted spot job; SKYPILOT_JOB_ID: {job_id_env_var}')
        self._cluster_name = spot_utils.generate_spot_cluster_name(
            self._task_name, self._job_id)
        self._strategy_executor = recovery_strategy.StrategyExecutor.make(
            self._cluster_name, self._backend, self._task, retry_until_up)

    def _run(self):
        """Busy loop monitoring spot cluster status and handling recovery."""
        logger.info(f'Started monitoring spot task {self._task_name} '
                    f'(id: {self._job_id})')
        spot_state.set_starting(self._job_id)
        job_submitted_at = self._strategy_executor.launch()

        spot_state.set_started(self._job_id, start_time=job_submitted_at)
        while True:
            time.sleep(spot_utils.JOB_STATUS_CHECK_GAP_SECONDS)

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
            job_status = spot_utils.get_job_status(self._backend,
                                                   self._cluster_name)

            if job_status == job_lib.JobStatus.SUCCEEDED:
                end_time = spot_utils.get_job_timestamp(self._backend,
                                                        self._cluster_name,
                                                        get_end_time=True)
                # The job is done.
                spot_state.set_succeeded(self._job_id, end_time=end_time)
                break

            # For single-node jobs, nonterminated job_status indicates a
            # healthy cluster. We can safely continue monitoring.
            # For multi-node jobs, since the job may not be set to FAILED
            # immediately (depending on user program) when only some of the
            # nodes are preempted, need to check the actual cluster status.
            if (job_status is not None and not job_status.is_terminal() and
                    self._task.num_nodes == 1):
                continue

            # Pull the actual cluster status from the cloud provider to
            # determine whether the cluster is preempted.
            (cluster_status,
             handle) = backend_utils.refresh_cluster_status_handle(
                 self._cluster_name, force_refresh=True)

            if cluster_status != global_user_state.ClusterStatus.UP:
                # The cluster is (partially) preempted. It can be down, INIT
                # or STOPPED, based on the interruption behavior of the cloud.
                # Spot recovery is needed (will be done later in the code).
                cluster_status_str = ('' if cluster_status is None else
                                      f' (status: {cluster_status.value})')
                logger.info(
                    f'Cluster is preempted{cluster_status_str}. Recovering...')
            else:
                if job_status is not None and not job_status.is_terminal():
                    # The multi-node job is still running, continue monitoring.
                    continue
                elif job_status in [
                        job_lib.JobStatus.FAILED, job_lib.JobStatus.FAILED_SETUP
                ]:
                    # The user code has probably crashed, fail immediately.
                    end_time = spot_utils.get_job_timestamp(self._backend,
                                                            self._cluster_name,
                                                            get_end_time=True)
                    logger.info(
                        'The user job failed. Please check the logs below.\n'
                        f'== Logs of the user job (ID: {self._job_id}) ==\n')
                    self._backend.tail_logs(handle,
                                            None,
                                            spot_job_id=self._job_id)
                    logger.info(f'\n== End of logs (ID: {self._job_id}) ==')
                    status_to_set = spot_state.SpotStatus.FAILED
                    if job_status == job_lib.JobStatus.FAILED_SETUP:
                        status_to_set = spot_state.SpotStatus.FAILED_SETUP
                    spot_state.set_failed(self._job_id,
                                          failure_type=status_to_set,
                                          end_time=end_time)
                    break
                # Although the cluster is healthy, we fail to access the
                # job status. Try to recover the job (will not restart the
                # cluster, if the cluster is healthy).
                assert job_status is None, job_status
                logger.info('Failed to fetch the job status while the '
                            'cluster is healthy. Try to recover the job '
                            '(the cluster will not be restarted).')

            # When the handle is None, the cluster should be cleaned up already.
            if handle is not None:
                resources = handle.launched_resources
                assert resources is not None, handle
                if resources.need_cleanup_after_preemption():
                    # Some spot resource (e.g., Spot TPU VM) may need to be
                    # cleaned up after preemption.
                    logger.info('Cleaning up the preempted spot cluster...')
                    self._strategy_executor.terminate_cluster()

            # Try to recover the spot jobs, when the cluster is preempted
            # or the job status is failed to be fetched.
            spot_state.set_recovering(self._job_id)
            recovered_time = self._strategy_executor.recover()
            spot_state.set_recovered(self._job_id,
                                     recovered_time=recovered_time)

    def run(self):
        """Run controller logic and handle exceptions."""
        try:
            self._run()
        except KeyboardInterrupt:
            # Kill the children processes launched by log_lib.run_with_log.
            subprocess_utils.kill_children_processes()
            spot_state.set_cancelled(self._job_id)
        except exceptions.ResourcesUnavailableError as e:
            logger.error(f'{common_utils.class_fullname(e.__class__)}: '
                         f'{colorama.Fore.RED}{e}{colorama.Style.RESET_ALL}')
            spot_state.set_failed(
                self._job_id,
                failure_type=spot_state.SpotStatus.FAILED_NO_RESOURCE)
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            logger.error(traceback.format_exc())
            logger.error('Unexpected error occurred: '
                         f'{common_utils.format_exception(e)}')
        finally:
            self._strategy_executor.terminate_cluster()
            job_status = spot_state.get_status(self._job_id)
            # The job can be non-terminal if the controller exited abnormally,
            # e.g. failed to launch cluster after reaching the MAX_RETRY.
            if not job_status.is_terminal():
                logger.info(f'Previous spot job status: {job_status.value}')
                spot_state.set_failed(
                    self._job_id,
                    failure_type=spot_state.SpotStatus.FAILED_CONTROLLER)

            # Clean up Storages with persistent=False.
            self._backend.teardown_ephemeral_storage(self._task)


def _run_controller(job_id: int, task_yaml: str, retry_until_up: bool):
    """Runs the controller in a remote process for interruption."""

    # Override the SIGTERM handler to gracefully terminate the controller.
    def handle_interupt(signum, frame):
        """Handle the interrupt signal."""
        # Need to raise KeyboardInterrupt to avoid the exception being caught by
        # the strategy executor.
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, handle_interupt)

    # The controller needs to be instantiated in the remote process, since
    # the controller is not serializable.
    spot_controller = SpotController(job_id, task_yaml, retry_until_up)
    spot_controller.run()


def _handle_signal(job_id):
    """Handle the signal if the user sent it."""
    signal_file = pathlib.Path(spot_utils.SIGNAL_FILE_PREFIX.format(job_id))
    user_signal = None
    if signal_file.exists():
        # Filelock is needed to prevent race condition with concurrent
        # signal writing.
        # TODO(mraheja): remove pylint disabling when filelock version
        # updated
        # pylint: disable=abstract-class-instantiated
        with filelock.FileLock(str(signal_file) + '.lock'):
            with signal_file.open(mode='r') as f:
                user_signal = f.read().strip()
                try:
                    user_signal = spot_utils.UserSignal(user_signal)
                except ValueError:
                    logger.warning(
                        f'Unknown signal received: {user_signal}. Ignoring.')
                    user_signal = None
            # Remove the signal file, after reading the signal.
            signal_file.unlink()
    if user_signal is None:
        # None or empty string.
        return
    assert user_signal == spot_utils.UserSignal.CANCEL, (
        f'Only cancel signal is supported, but {user_signal} got.')
    raise exceptions.SpotUserCancelledError(
        f'User sent {user_signal.value} signal.')


def start(job_id, task_yaml, retry_until_up):
    """Start the controller."""
    controller_process = None
    try:
        _handle_signal(job_id)
        controller_process = multiprocessing.Process(target=_run_controller,
                                                     args=(job_id, task_yaml,
                                                           retry_until_up))
        controller_process.start()
        while controller_process.is_alive():
            _handle_signal(job_id)
            time.sleep(1)
    except exceptions.SpotUserCancelledError:
        logger.info(f'Cancelling spot job {job_id}...')
        if controller_process is not None:
            logger.info('Sending SIGTERM to controller process '
                        f'{controller_process.pid}')
            # This will raise KeyboardInterrupt in the task.
            # Using SIGTERM instead of SIGINT, as the SIGINT is weirdly ignored
            # by the controller process when it is started inside a ray job.
            controller_process.terminate()
    if controller_process is not None:
        controller_process.join()


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
    start(args.job_id, args.task_yaml, args.retry_until_up)
