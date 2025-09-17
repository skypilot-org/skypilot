"""gRPC service implementations for skylet."""

import os

import grpc

from sky import sky_logging
from sky.jobs import state as managed_job_state
from sky.schemas.generated import autostopv1_pb2
from sky.schemas.generated import autostopv1_pb2_grpc
from sky.schemas.generated import jobsv1_pb2
from sky.schemas.generated import jobsv1_pb2_grpc
from sky.schemas.generated import servev1_pb2
from sky.schemas.generated import servev1_pb2_grpc
from sky.serve import serve_rpc_utils
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.skylet import job_lib

logger = sky_logging.init_logger(__name__)


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
            autostop_lib.set_autostop(
                idle_minutes=request.idle_minutes,
                backend=request.backend,
                wait_for=wait_for if wait_for is not None else
                autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                down=request.down)
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

            cd = f'cd {constants.SKY_REMOTE_WORKDIR}'
            job_submit_cmd = (
                # JOB_CMD_IDENTIFIER is used for identifying the process
                # retrieved with pid is the same driver process.
                f'{job_lib.JOB_CMD_IDENTIFIER.format(job_id)} && '
                f'{cd} && {constants.SKY_PYTHON_CMD} -u {script_path}'
                # Do not use &>, which is not POSIX and may not work.
                # Note that the order of ">filename 2>&1" matters.
                f' > {remote_log_path} 2>&1')
            job_lib.scheduler.queue(job_id, job_submit_cmd)

            if request.HasField('managed_job'):
                managed_job = request.managed_job
                pool = managed_job.pool if managed_job.HasField(
                    'pool') else None
                pool_hash = None
                if pool is not None:
                    pool_hash = serve_state.get_service_hash(pool)
                # Add the managed job to job queue database.
                managed_job_state.set_job_info(job_id, managed_job.name,
                                               managed_job.workspace,
                                               managed_job.entrypoint, pool,
                                               pool_hash)
                # Set the managed job to PENDING state to make sure that
                # this managed job appears in the `sky jobs queue`, even
                # if it needs to wait to be submitted.
                # We cannot set the managed job to PENDING state in the
                # job template (jobs-controller.yaml.j2), as it may need
                # to wait for the run commands to be scheduled on the job
                # controller in high-load cases.
                for task in managed_job.tasks:
                    managed_job_state.set_pending(job_id, task.task_id,
                                                  task.name, task.resources_str,
                                                  task.metadata_json)
            return jobsv1_pb2.QueueJobResponse()
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
        # TODO(kevin): implement this
        raise NotImplementedError('TailLogs is not implemented')

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
