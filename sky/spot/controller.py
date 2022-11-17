"""Controller: handles the life cycle of a managed spot cluster (job)."""
import argparse
import pathlib
import time
import traceback

import colorama
import filelock
import ray

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

logger = sky_logging.init_logger(__name__)


class SpotController:
    """Each spot controller manages the life cycle of one spot cluster (job)."""

    def __init__(self, job_id: int, task_yaml: str,
                 retry_until_up: bool) -> None:
        self.job_id = job_id
        self.task_name = pathlib.Path(task_yaml).stem
        self.task = sky.Task.from_yaml(task_yaml)

        self._retry_until_up = retry_until_up
        # TODO(zhwu): this assumes the specific backend.
        self.backend = cloud_vm_ray_backend.CloudVmRayBackend()

        # Add a unique identifier to the task environment variables, so that
        # the user can have the same id for multiple recoveries.
        #   Example value: sky-2022-10-04-22-46-52-467694_id-17
        task_envs = self.task.envs or {}
        job_id_env_var = common_utils.get_global_job_id(
            self.backend.run_timestamp, 'spot', self.job_id)
        task_envs[constants.JOB_ID_ENV_VAR] = job_id_env_var
        self.task.set_envs(task_envs)

        spot_state.set_submitted(
            self.job_id,
            self.task_name,
            self.backend.run_timestamp,
            resources_str=backend_utils.get_task_resources_str(self.task))
        logger.info(f'Submitted spot job; SKYPILOT_JOB_ID: {job_id_env_var}')
        self.cluster_name = spot_utils.generate_spot_cluster_name(
            self.task_name, self.job_id)
        self.strategy_executor = recovery_strategy.StrategyExecutor.make(
            self.cluster_name, self.backend, self.task, retry_until_up)

    def start(self):
        """Start the controller."""
        try:
            self._handle_signal()
            controller_task = _controller_run.remote(self)
            # Signal can interrupt the underlying controller process.
            ready, _ = ray.wait([controller_task], timeout=0)
            while not ready:
                try:
                    self._handle_signal()
                except exceptions.SpotUserCancelledError as e:
                    logger.info('Cancelling...')
                    try:
                        ray.cancel(controller_task)
                        ray.get(controller_task)
                    except ray.exceptions.RayTaskError:
                        # When the controller task is cancelled, it will raise
                        # ray.exceptions.RayTaskError, which can be ignored,
                        # since the SpotUserCancelledError will be raised and
                        # handled later.
                        pass
                    raise e
                ready, _ = ray.wait([controller_task], timeout=1)
            # Need this to get the exception from the controller task.
            ray.get(controller_task)
        except exceptions.SpotUserCancelledError as e:
            logger.info(e)
            spot_state.set_cancelled(self.job_id)
        except exceptions.ResourcesUnavailableError as e:
            logger.error(f'Resources unavailable: {colorama.Fore.RED}{e}'
                         f'{colorama.Style.RESET_ALL}')
            spot_state.set_failed(
                self.job_id,
                failure_type=spot_state.SpotStatus.FAILED_NO_RESOURCE)
        except (Exception, SystemExit) as e:  # pylint: disable=broad-except
            logger.error(traceback.format_exc())
            logger.error(f'Unexpected error occurred: {type(e).__name__}: {e}')
        finally:
            self.strategy_executor.terminate_cluster()
            job_status = spot_state.get_status(self.job_id)
            # The job can be non-terminal if the controller exited abnormally,
            # e.g. failed to launch cluster after reaching the MAX_RETRY.
            if not job_status.is_terminal():
                spot_state.set_failed(
                    self.job_id,
                    failure_type=spot_state.SpotStatus.FAILED_CONTROLLER)

            # Clean up Storages with persistent=False.
            self.backend.teardown_ephemeral_storage(self.task)

    def _handle_signal(self):
        """Handle the signal if the user sent it."""
        signal_file = pathlib.Path(
            spot_utils.SIGNAL_FILE_PREFIX.format(self.job_id))
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


@ray.remote(num_cpus=0)
def _controller_run(spot_controller: SpotController):
    """Busy loop monitoring spot cluster status and handling recovery."""
    logger.info(f'Started monitoring spot task {spot_controller.task_name} '
                f'(id: {spot_controller.job_id})')
    spot_state.set_starting(spot_controller.job_id)
    start_at = spot_controller.strategy_executor.launch()

    spot_state.set_started(spot_controller.job_id, start_time=start_at)
    while True:
        time.sleep(spot_utils.JOB_STATUS_CHECK_GAP_SECONDS)

        # Check the network connection to avoid false alarm for job failure.
        # Network glitch was observed even in the VM.
        try:
            backend_utils.check_network_connection()
        except exceptions.NetworkError:
            logger.info('Network is not available. Retrying again in '
                        f'{spot_utils.JOB_STATUS_CHECK_GAP_SECONDS} seconds.')
            continue

        # NOTE: we do not check cluster status first because race condition
        # can occur, i.e. cluster can be down during the job status check.
        job_status = spot_utils.get_job_status(spot_controller.backend,
                                               spot_controller.cluster_name)

        if job_status is not None and not job_status.is_terminal():
            need_recovery = False
            if spot_controller.task.num_nodes > 1:
                # Check the cluster status for multi-node jobs, since the
                # job may not be set to FAILED immediately when only some
                # of the nodes are preempted.
                (cluster_status,
                 handle) = backend_utils.refresh_cluster_status_handle(
                     spot_controller.cluster_name, force_refresh=True)
                if cluster_status != global_user_state.ClusterStatus.UP:
                    # recover the cluster if it is not up.
                    logger.info(f'Cluster status {cluster_status.value}. '
                                'Recovering...')
                    need_recovery = True
            if not need_recovery:
                # The job and cluster are healthy, continue to monitor the
                # job status.
                continue

        if job_status == job_lib.JobStatus.SUCCEEDED:
            end_time = spot_utils.get_job_timestamp(
                spot_controller.backend,
                spot_controller.cluster_name,
                get_end_time=True)
            # The job is done.
            spot_state.set_succeeded(spot_controller.job_id, end_time=end_time)
            break

        if job_status == job_lib.JobStatus.FAILED:
            # Check the status of the spot cluster. If it is not UP,
            # the cluster is preempted.
            (cluster_status,
             handle) = backend_utils.refresh_cluster_status_handle(
                 spot_controller.cluster_name, force_refresh=True)
            if cluster_status == global_user_state.ClusterStatus.UP:
                # The user code has probably crashed.
                end_time = spot_utils.get_job_timestamp(
                    spot_controller.backend,
                    spot_controller.cluster_name,
                    get_end_time=True)
                logger.info(
                    'The user job failed. Please check the logs below.\n'
                    '== Logs of the user job (ID: '
                    f'{spot_controller.job_id}) ==\n')
                spot_controller.backend.tail_logs(
                    handle, None, spot_job_id=spot_controller.job_id)
                logger.info(
                    f'\n== End of logs (ID: {spot_controller.job_id}) ==')
                spot_state.set_failed(spot_controller.job_id,
                                      failure_type=spot_state.SpotStatus.FAILED,
                                      end_time=end_time)
                break
        # cluster can be down, INIT or STOPPED, based on the interruption
        # behavior of the cloud.
        # Failed to connect to the cluster or the cluster is partially down.
        # job_status is None or job_status == job_lib.JobStatus.FAILED
        logger.info('The cluster is preempted.')
        spot_state.set_recovering(spot_controller.job_id)
        recovered_time = spot_controller.strategy_executor.recover()
        spot_state.set_recovered(spot_controller.job_id,
                                 recovered_time=recovered_time)


if __name__ == '__main__':
    ray.init('auto')
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
