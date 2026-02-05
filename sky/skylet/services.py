"""gRPC service implementations for skylet."""

import os
from typing import List, Optional

import grpc

from sky import exceptions
from sky import sky_logging
from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.schemas.generated import autostopv1_pb2
from sky.schemas.generated import autostopv1_pb2_grpc
from sky.schemas.generated import jobsv1_pb2
from sky.schemas.generated import jobsv1_pb2_grpc
from sky.schemas.generated import managed_jobsv1_pb2
from sky.schemas.generated import managed_jobsv1_pb2_grpc
from sky.schemas.generated import servev1_pb2
from sky.schemas.generated import servev1_pb2_grpc
from sky.serve import serve_rpc_utils
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.skylet import job_lib
from sky.skylet import log_lib

logger = sky_logging.init_logger(__name__)

# In the worst case, flush the log buffer every 50ms,
# to ensure responsiveness.
DEFAULT_LOG_CHUNK_FLUSH_INTERVAL = 0.05


class AutostopServiceImpl(autostopv1_pb2_grpc.AutostopServiceServicer):
    """Implementation of the AutostopService gRPC service."""

    def SetAutostop(  # type: ignore[return]
            self, request: autostopv1_pb2.SetAutostopRequest,
            context: grpc.ServicerContext
    ) -> autostopv1_pb2.SetAutostopResponse:
        """Sets autostop configuration for the cluster."""
        try:
            wait_for = autostop_lib.AutostopWaitFor.from_protobuf(
                request.wait_for)
            hook = request.hook if request.HasField('hook') else None
            hook_timeout = (request.hook_timeout
                            if request.HasField('hook_timeout') else None)
            autostop_lib.set_autostop(
                idle_minutes=request.idle_minutes,
                backend=request.backend,
                wait_for=wait_for if wait_for is not None else
                autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                down=request.down,
                hook=hook,
                hook_timeout=hook_timeout)
            return autostopv1_pb2.SetAutostopResponse()
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def IsAutostopping(  # type: ignore[return]
            self, request: autostopv1_pb2.IsAutostoppingRequest,
            context: grpc.ServicerContext
    ) -> autostopv1_pb2.IsAutostoppingResponse:
        """Checks if the cluster is currently autostopping."""
        try:
            is_autostopping = autostop_lib.get_is_autostopping()
            return autostopv1_pb2.IsAutostoppingResponse(
                is_autostopping=is_autostopping)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))


class ServeServiceImpl(servev1_pb2_grpc.ServeServiceServicer):
    """Implementation of the ServeService gRPC service."""

    # NOTE (kyuds): this grpc service will run cluster-side,
    # thus guaranteeing that SERVE_VERSION is above 5.
    # Therefore, we removed some SERVE_VERSION checks
    # present in the original codegen.

    def GetServiceStatus(  # type: ignore[return]
            self, request: servev1_pb2.GetServiceStatusRequest,
            context: grpc.ServicerContext
    ) -> servev1_pb2.GetServiceStatusResponse:
        """Gets serve status."""
        try:
            service_names, pool = (
                serve_rpc_utils.GetServiceStatusRequestConverter.from_proto(request))  # pylint: disable=line-too-long
            statuses = serve_utils.get_service_status_pickled(
                service_names, pool)
            return serve_rpc_utils.GetServiceStatusResponseConverter.to_proto(
                statuses)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def AddVersion(  # type: ignore[return]
            self, request: servev1_pb2.AddVersionRequest,
            context: grpc.ServicerContext) -> servev1_pb2.AddVersionResponse:
        """Adds serve version"""
        try:
            service_name = request.service_name
            version = serve_state.add_version(service_name)
            return servev1_pb2.AddVersionResponse(version=version)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def TerminateServices(  # type: ignore[return]
            self, request: servev1_pb2.TerminateServicesRequest,
            context: grpc.ServicerContext
    ) -> servev1_pb2.TerminateServicesResponse:
        """Terminates serve"""
        try:
            service_names, purge, pool = (
                serve_rpc_utils.TerminateServicesRequestConverter.from_proto(request))  # pylint: disable=line-too-long
            message = serve_utils.terminate_services(service_names, purge, pool)
            return servev1_pb2.TerminateServicesResponse(message=message)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def TerminateReplica(  # type: ignore[return]
            self, request: servev1_pb2.TerminateReplicaRequest,
            context: grpc.ServicerContext
    ) -> servev1_pb2.TerminateReplicaResponse:
        """Terminate replica"""
        try:
            service_name = request.service_name
            replica_id = request.replica_id
            purge = request.purge
            message = serve_utils.terminate_replica(service_name, replica_id,
                                                    purge)
            return servev1_pb2.TerminateReplicaResponse(message=message)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def WaitServiceRegistration(  # type: ignore[return]
        self, request: servev1_pb2.WaitServiceRegistrationRequest,
        context: grpc.ServicerContext
    ) -> servev1_pb2.WaitServiceRegistrationResponse:
        """Wait for service to be registered"""
        try:
            service_name = request.service_name
            job_id = request.job_id
            pool = request.pool
            encoded = serve_utils.wait_service_registration(
                service_name, job_id, pool)
            lb_port = serve_utils.load_service_initialization_result(encoded)
            return servev1_pb2.WaitServiceRegistrationResponse(lb_port=lb_port)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def UpdateService(  # type: ignore[return]
            self, request: servev1_pb2.UpdateServiceRequest,
            context: grpc.ServicerContext) -> servev1_pb2.UpdateServiceResponse:
        """Update service"""
        try:
            service_name = request.service_name
            version = request.version
            mode = request.mode
            pool = request.pool
            serve_utils.update_service_encoded(service_name, version, mode,
                                               pool)
            return servev1_pb2.UpdateServiceResponse()
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))


class JobsServiceImpl(jobsv1_pb2_grpc.JobsServiceServicer):
    """Implementation of the JobsService gRPC service."""

    def AddJob(  # type: ignore[return]
            self, request: jobsv1_pb2.AddJobRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.AddJobResponse:
        try:
            job_name = request.job_name if request.HasField('job_name') else '-'
            job_id, log_dir = job_lib.add_job(job_name, request.username,
                                              request.run_timestamp,
                                              request.resources_str,
                                              request.metadata)
            return jobsv1_pb2.AddJobResponse(job_id=job_id, log_dir=log_dir)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def QueueJob(  # type: ignore[return]
            self, request: jobsv1_pb2.QueueJobRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.QueueJobResponse:
        try:
            job_id = request.job_id
            # Create log directory and file
            remote_log_dir = os.path.expanduser(request.remote_log_dir)
            os.makedirs(remote_log_dir, exist_ok=True)
            remote_log_path = os.path.join(remote_log_dir, 'run.log')
            open(remote_log_path, 'a').close()  # pylint: disable=unspecified-encoding

            script_path = os.path.expanduser(request.script_path)
            os.makedirs(os.path.dirname(script_path), exist_ok=True)

            # If `codegen` is not provided, assume script is already
            # uploaded to `script_path` via rsync.
            if request.HasField('codegen'):
                with open(script_path, 'w', encoding='utf-8') as f:
                    f.write(request.codegen)
                os.chmod(script_path, 0o755)

            job_submit_cmd = (
                # JOB_CMD_IDENTIFIER is used for identifying the process
                # retrieved with pid is the same driver process.
                f'{job_lib.JOB_CMD_IDENTIFIER.format(job_id)} && '
                f'{constants.SKY_PYTHON_CMD} -u {script_path}'
                # Do not use &>, which is not POSIX and may not work.
                # Note that the order of ">filename 2>&1" matters.
                f' > {remote_log_path} 2>&1')
            job_lib.scheduler.queue(job_id, job_submit_cmd)
            return jobsv1_pb2.QueueJobResponse()
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def SetJobInfoWithoutJobId(  # type: ignore[return]
        self, request: jobsv1_pb2.SetJobInfoWithoutJobIdRequest,
        context: grpc.ServicerContext
    ) -> jobsv1_pb2.SetJobInfoWithoutJobIdResponse:
        try:
            pool = request.pool if request.HasField('pool') else None
            pool_hash = request.pool_hash if request.HasField(
                'pool_hash') else None
            user_hash = request.user_hash if request.HasField(
                'user_hash') else None
            job_ids = []
            execution = request.execution
            for i in range(request.num_jobs):
                is_primary_in_job_group = request.is_primary_in_job_groups[i]
                job_id = managed_job_state.set_job_info_without_job_id(
                    name=request.name,
                    workspace=request.workspace,
                    entrypoint=request.entrypoint,
                    pool=pool,
                    pool_hash=pool_hash,
                    user_hash=user_hash,
                    execution=execution)
                job_ids.append(job_id)
                # Set pending state for all tasks
                for task_id, task_name, metadata_json in zip(
                        request.task_ids, request.task_names,
                        request.metadata_jsons):
                    managed_job_state.set_pending(job_id, task_id, task_name,
                                                  request.resources_str,
                                                  metadata_json,
                                                  is_primary_in_job_group)
            return jobsv1_pb2.SetJobInfoWithoutJobIdResponse(job_ids=job_ids)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def UpdateStatus(  # type: ignore[return]
            self, request: jobsv1_pb2.UpdateStatusRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.UpdateStatusResponse:
        try:
            job_lib.update_status()
            return jobsv1_pb2.UpdateStatusResponse()
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetJobQueue(  # type: ignore[return]
            self, request: jobsv1_pb2.GetJobQueueRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.GetJobQueueResponse:
        try:
            user_hash = request.user_hash if request.HasField(
                'user_hash') else None
            all_jobs = request.all_jobs
            jobs_info = job_lib.get_jobs_info(user_hash=user_hash,
                                              all_jobs=all_jobs)
            return jobsv1_pb2.GetJobQueueResponse(jobs=jobs_info)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def CancelJobs(  # type: ignore[return]
            self, request: jobsv1_pb2.CancelJobsRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.CancelJobsResponse:
        try:
            job_ids = list(request.job_ids) if request.job_ids else []
            user_hash = request.user_hash if request.HasField(
                'user_hash') else None
            cancelled_job_ids = job_lib.cancel_jobs(job_ids, request.cancel_all,
                                                    user_hash)
            return jobsv1_pb2.CancelJobsResponse(
                cancelled_job_ids=cancelled_job_ids)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def FailAllInProgressJobs(  # type: ignore[return]
        self, _: jobsv1_pb2.FailAllInProgressJobsRequest,
        context: grpc.ServicerContext
    ) -> jobsv1_pb2.FailAllInProgressJobsResponse:
        try:
            job_lib.fail_all_jobs_in_progress()
            return jobsv1_pb2.FailAllInProgressJobsResponse()
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def TailLogs(
            self,
            request: jobsv1_pb2.TailLogsRequest,  # type: ignore[return]
            context: grpc.ServicerContext):
        buffer = log_lib.LogBuffer()
        try:
            job_id = request.job_id if request.HasField(
                'job_id') else job_lib.get_latest_job_id()
            managed_job_id = request.managed_job_id if request.HasField(
                'managed_job_id') else None
            log_dir = job_lib.get_log_dir_for_job(job_id)
            if log_dir is None:
                run_timestamp = job_lib.get_run_timestamp(job_id)
                log_dir = None if run_timestamp is None else os.path.join(
                    constants.SKY_LOGS_DIRECTORY, run_timestamp)

            for line in log_lib.buffered_iter_with_timeout(
                    buffer,
                    log_lib.tail_logs_iter(job_id, log_dir, managed_job_id,
                                           request.follow, request.tail),
                    DEFAULT_LOG_CHUNK_FLUSH_INTERVAL):
                yield jobsv1_pb2.TailLogsResponse(log_line=line)

            job_status = job_lib.get_status(job_id)
            exit_code = exceptions.JobExitCode.from_job_status(job_status)
            # Fix for dashboard: When follow=False and job is still running
            # (NOT_FINISHED=101), exit with success (0) since fetching current
            # logs is a successful operation.
            # This prevents shell wrappers from printing "command terminated
            # with exit code 101".
            exit_code_int = 0 if not request.follow and int(
                exit_code) == 101 else int(exit_code)
            yield jobsv1_pb2.TailLogsResponse(exit_code=exit_code_int)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))
        finally:
            buffer.close()

    def GetJobStatus(  # type: ignore[return]
            self, request: jobsv1_pb2.GetJobStatusRequest,
            context: grpc.ServicerContext) -> jobsv1_pb2.GetJobStatusResponse:
        try:
            if request.job_ids:
                job_ids = list(request.job_ids)
            else:
                latest_job_id = job_lib.get_latest_job_id()
                job_ids = [latest_job_id] if latest_job_id is not None else []
            job_statuses = job_lib.get_statuses(job_ids)
            for job_id, status in job_statuses.items():
                job_statuses[job_id] = job_lib.JobStatus(status).to_protobuf(
                ) if status is not None else jobsv1_pb2.JOB_STATUS_UNSPECIFIED
            return jobsv1_pb2.GetJobStatusResponse(job_statuses=job_statuses)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetJobSubmittedTimestamp(  # type: ignore[return]
        self, request: jobsv1_pb2.GetJobSubmittedTimestampRequest,
        context: grpc.ServicerContext
    ) -> jobsv1_pb2.GetJobSubmittedTimestampResponse:
        try:
            job_id = request.job_id if request.HasField(
                'job_id') else job_lib.get_latest_job_id()
            timestamp = job_lib.get_job_submitted_or_ended_timestamp(
                job_id, False)
            if timestamp is None:
                context.abort(grpc.StatusCode.NOT_FOUND,
                              f'Job {job_id} not found')
            return jobsv1_pb2.GetJobSubmittedTimestampResponse(
                timestamp=timestamp)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetJobEndedTimestamp(  # type: ignore[return]
        self, request: jobsv1_pb2.GetJobEndedTimestampRequest,
        context: grpc.ServicerContext
    ) -> jobsv1_pb2.GetJobEndedTimestampResponse:
        try:
            job_id = request.job_id if request.HasField(
                'job_id') else job_lib.get_latest_job_id()
            timestamp = job_lib.get_job_submitted_or_ended_timestamp(
                job_id, True)
            if timestamp is None:
                context.abort(grpc.StatusCode.NOT_FOUND,
                              f'Job {job_id} not found or not ended')
            return jobsv1_pb2.GetJobEndedTimestampResponse(timestamp=timestamp)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetLogDirsForJobs(  # type: ignore[return]
            self, request: jobsv1_pb2.GetLogDirsForJobsRequest,
            context: grpc.ServicerContext
    ) -> jobsv1_pb2.GetLogDirsForJobsResponse:
        try:
            if request.job_ids:
                job_ids = list(request.job_ids)
            else:
                latest_job_id = job_lib.get_latest_job_id()
                job_ids = [latest_job_id] if latest_job_id is not None else []
            job_log_dirs = job_lib.get_job_log_dirs(job_ids)
            return jobsv1_pb2.GetLogDirsForJobsResponse(
                job_log_dirs=job_log_dirs)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetJobExitCodes(  # type: ignore[return]
            self, request: jobsv1_pb2.GetJobExitCodesRequest,
            context: grpc.ServicerContext
    ) -> jobsv1_pb2.GetJobExitCodesResponse:
        try:
            job_id = request.job_id if request.HasField(
                'job_id') else job_lib.get_latest_job_id()
            exit_codes: Optional[List[int]] = None
            if job_id:
                exit_codes_list = job_lib.get_exit_codes(job_id)
                exit_codes = exit_codes_list if exit_codes_list else []
            return jobsv1_pb2.GetJobExitCodesResponse(exit_codes=exit_codes)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))


class ManagedJobsServiceImpl(managed_jobsv1_pb2_grpc.ManagedJobsServiceServicer
                            ):
    """Implementation of the ManagedJobsService gRPC service."""

    def GetVersion(  # type: ignore[return]
            self, request: managed_jobsv1_pb2.GetVersionRequest,
            context: grpc.ServicerContext
    ) -> managed_jobsv1_pb2.GetVersionResponse:
        try:
            return managed_jobsv1_pb2.GetVersionResponse(
                controller_version=constants.SKYLET_VERSION)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetJobTable(  # type: ignore[return]
        self, request: managed_jobsv1_pb2.GetJobTableRequest,
        context: grpc.ServicerContext
    ) -> managed_jobsv1_pb2.GetJobTableResponse:
        try:
            accessible_workspaces = (
                list(request.accessible_workspaces.workspaces)
                if request.HasField('accessible_workspaces') else None)
            job_ids = (list(request.job_ids.ids)
                       if request.HasField('job_ids') else None)
            user_hashes: Optional[List[Optional[str]]] = None
            if request.HasField('user_hashes'):
                user_hashes = list(request.user_hashes.hashes)
                # For backwards compatibility, we show jobs that do not have a
                # user_hash. TODO: Remove before 0.12.0.
                if request.show_jobs_without_user_hash:
                    user_hashes.append(None)
            statuses = (list(request.statuses.statuses)
                        if request.HasField('statuses') else None)
            fields = (list(request.fields.fields)
                      if request.HasField('fields') else None)
            job_queue = managed_job_utils.get_managed_job_queue(
                skip_finished=request.skip_finished,
                accessible_workspaces=accessible_workspaces,
                job_ids=job_ids,
                workspace_match=request.workspace_match
                if request.HasField('workspace_match') else None,
                name_match=request.name_match
                if request.HasField('name_match') else None,
                pool_match=request.pool_match
                if request.HasField('pool_match') else None,
                page=request.page if request.HasField('page') else None,
                limit=request.limit if request.HasField('limit') else None,
                user_hashes=user_hashes,
                statuses=statuses,
                fields=fields,
            )
            jobs = job_queue['jobs']
            total = job_queue['total']
            total_no_filter = job_queue['total_no_filter']
            status_counts = job_queue['status_counts']

            jobs_info = []
            for job in jobs:
                converted_metadata = None
                metadata = job.get('metadata')
                if metadata:
                    converted_metadata = {
                        k: v for k, v in metadata.items() if v is not None
                    }
                schedule_state = job.get('schedule_state')
                if schedule_state is not None:
                    schedule_state = managed_job_state.ManagedJobScheduleState(
                        schedule_state).to_protobuf()
                job_info = managed_jobsv1_pb2.ManagedJobInfo(
                    # The `spot.job_id`, which can be used to identify
                    # different tasks for the same job
                    _job_id=job.get('_job_id'),
                    job_id=job.get('job_id'),
                    task_id=job.get('task_id'),
                    job_name=job.get('job_name'),
                    task_name=job.get('task_name'),
                    job_duration=job.get('job_duration'),
                    workspace=job.get('workspace'),
                    status=managed_job_state.ManagedJobStatus(
                        job.get('status')).to_protobuf(),
                    schedule_state=schedule_state,
                    resources=job.get('resources'),
                    cluster_resources=job.get('cluster_resources'),
                    cluster_resources_full=job.get('cluster_resources_full'),
                    cloud=job.get('cloud'),
                    region=job.get('region'),
                    infra=job.get('infra'),
                    accelerators=job.get('accelerators'),
                    recovery_count=job.get('recovery_count'),
                    details=job.get('details'),
                    failure_reason=job.get('failure_reason'),
                    user_name=job.get('user_name'),
                    user_hash=job.get('user_hash'),
                    submitted_at=job.get('submitted_at'),
                    start_at=job.get('start_at'),
                    end_at=job.get('end_at'),
                    user_yaml=job.get('user_yaml'),
                    entrypoint=job.get('entrypoint'),
                    metadata=converted_metadata,
                    pool=job.get('pool'),
                    pool_hash=job.get('pool_hash'),
                    links=job.get('links'),
                    # Primary/auxiliary task support (None for non-job-groups)
                    is_primary_in_job_group=job.get('is_primary_in_job_group'),
                    # Fields populated from cluster handle
                    zone=job.get('zone'),
                    labels=job.get('labels'),
                    cluster_name_on_cloud=job.get('cluster_name_on_cloud'))
                jobs_info.append(job_info)

            return managed_jobsv1_pb2.GetJobTableResponse(
                jobs=jobs_info,
                total=total,
                total_no_filter=total_no_filter,
                status_counts=status_counts)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(e, exc_info=True)
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetAllJobIdsByName(  # type: ignore[return]
        self, request: managed_jobsv1_pb2.GetAllJobIdsByNameRequest,
        context: grpc.ServicerContext
    ) -> managed_jobsv1_pb2.GetAllJobIdsByNameResponse:
        try:
            job_name = request.job_name if request.HasField(
                'job_name') else None
            job_ids = managed_job_state.get_all_job_ids_by_name(job_name)
            return managed_jobsv1_pb2.GetAllJobIdsByNameResponse(
                job_ids=job_ids)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def CancelJobs(  # type: ignore[return]
            self, request: managed_jobsv1_pb2.CancelJobsRequest,
            context: grpc.ServicerContext
    ) -> managed_jobsv1_pb2.CancelJobsResponse:
        try:
            cancellation_criteria = request.WhichOneof('cancellation_criteria')
            if cancellation_criteria is None:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    'exactly one cancellation criteria must be specified.')

            if cancellation_criteria == 'all_users':
                user_hash = request.user_hash if request.HasField(
                    'user_hash') else None
                all_users = request.all_users
                if not all_users and user_hash is None:
                    context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        'user_hash is required when all_users is False')
                message = managed_job_utils.cancel_jobs_by_id(
                    job_ids=None,
                    all_users=all_users,
                    current_workspace=request.current_workspace,
                    user_hash=user_hash)
            elif cancellation_criteria == 'job_ids':
                job_ids = list(request.job_ids.ids)
                message = managed_job_utils.cancel_jobs_by_id(
                    job_ids=job_ids,
                    current_workspace=request.current_workspace)
            elif cancellation_criteria == 'job_name':
                message = managed_job_utils.cancel_job_by_name(
                    job_name=request.job_name,
                    current_workspace=request.current_workspace)
            elif cancellation_criteria == 'pool_name':
                message = managed_job_utils.cancel_jobs_by_pool(
                    pool_name=request.pool_name,
                    current_workspace=request.current_workspace)
            else:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f'invalid cancellation criteria: {cancellation_criteria}')
            return managed_jobsv1_pb2.CancelJobsResponse(message=message)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def StreamLogs(
            self,
            request: managed_jobsv1_pb2.
        StreamLogsRequest,  # type: ignore[return]
            context: grpc.ServicerContext):
        # TODO(kevin): implement this
        context.abort(grpc.StatusCode.UNIMPLEMENTED,
                      'StreamLogs is not implemented')
