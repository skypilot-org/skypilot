"""Utility functions for managed jobs."""
import typing

from sky import backends
from sky import exceptions
from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.jobs import utils as managed_job_utils
from sky.skylet import constants as skylet_constants
from sky.utils import controller_utils

logger = sky_logging.init_logger(__name__)

if typing.TYPE_CHECKING:
    from sky.schemas.generated import managed_jobsv1_pb2
else:
    managed_jobsv1_pb2 = adaptors_common.LazyImport(
        'sky.schemas.generated.managed_jobsv1_pb2')

_MANAGED_JOB_FIELDS_TO_GET = [
    'job_id', 'task_id', 'workspace', 'job_name', 'task_name', 'resources',
    'submitted_at', 'end_at', 'job_duration', 'recovery_count', 'status', 'pool'
]


def check_version_mismatch_and_non_terminal_jobs() -> None:
    """Check if controller has version mismatch and non-terminal jobs exist.
    Raises:
        ValueError: If there's a version mismatch and non-terminal jobs exist.
        sky.exceptions.ClusterNotUpError: If the controller is not accessible.
    """
    # Get the current local SKYLET_VERSION
    local_version = skylet_constants.SKYLET_VERSION

    # Get controller handle (works the same in both normal and
    # consolidation mode)
    jobs_controller_type = controller_utils.Controllers.JOBS_CONTROLLER
    handle = backend_utils.is_controller_accessible(
        controller=jobs_controller_type,
        stopped_message='Jobs controller is not running.')

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    use_legacy = not handle.is_grpc_enabled_with_flag

    if not use_legacy:
        try:
            version_request = managed_jobsv1_pb2.GetVersionRequest()
            version_response = backend_utils.invoke_skylet_with_retries(
                lambda: cloud_vm_ray_backend.SkyletClient(
                    handle.get_grpc_channel(
                    )).get_managed_job_controller_version(version_request))
            controller_version = version_response.controller_version

            job_table_request = managed_jobsv1_pb2.GetJobTableRequest(
                skip_finished=True,
                fields=managed_jobsv1_pb2.Fields(
                    fields=_MANAGED_JOB_FIELDS_TO_GET),
            )
            job_table_response = backend_utils.invoke_skylet_with_retries(
                lambda: cloud_vm_ray_backend.SkyletClient(
                    handle.get_grpc_channel()).get_managed_job_table(
                        job_table_request))
            jobs = managed_job_utils.decode_managed_job_protos(
                job_table_response.jobs)
        except exceptions.SkyletMethodNotImplementedError:
            use_legacy = True

    if use_legacy:
        # Get controller version and raw job table
        code = managed_job_utils.ManagedJobCodeGen.get_version()

        returncode, output, stderr = backend.run_on_head(handle,
                                                         code,
                                                         require_outputs=True,
                                                         stream_logs=False,
                                                         separate_stderr=True)

        if returncode != 0:
            logger.error(output + stderr)
            raise ValueError('Failed to check controller version with '
                             f'returncode: {returncode}.\n{output + stderr}')

        # Parse the output to extract controller version (split only on first
        # newline)
        output_parts = output.strip().split('\n', 1)

        # Extract controller version from first line
        if not output_parts[0].startswith('controller_version:'):
            raise ValueError(
                f'Expected controller version in first line, got: {output}')

        controller_version = output_parts[0].split(':', 1)[1]

        code = managed_job_utils.ManagedJobCodeGen.get_job_table(
            skip_finished=True, fields=_MANAGED_JOB_FIELDS_TO_GET)
        returncode, job_table_payload, stderr = backend.run_on_head(
            handle,
            code,
            require_outputs=True,
            stream_logs=False,
            separate_stderr=True)

        if returncode != 0:
            logger.error(job_table_payload + stderr)
            raise ValueError('Failed to fetch managed jobs with returncode: '
                             f'{returncode}.\n{job_table_payload + stderr}')

        jobs, _, _, _, _ = (
            managed_job_utils.load_managed_job_queue(job_table_payload))

    # Process locally: check version match and filter non-terminal jobs
    version_matches = (controller_version == local_version or
                       int(controller_version) > 17)
    non_terminal_jobs = [job for job in jobs if not job['status'].is_terminal()]
    has_non_terminal_jobs = len(non_terminal_jobs) > 0

    if not version_matches and has_non_terminal_jobs:
        # Format job table locally using the same method as queue()
        formatted_job_table = managed_job_utils.format_job_table(
            non_terminal_jobs,
            pool_status=None,
            show_all=False,
            show_user=False)

        error_msg = (
            f'Controller SKYLET_VERSION ({controller_version}) does not match '
            f'current version ({local_version}), and there are non-terminal '
            'jobs on the controller. Please wait for all jobs to complete or '
            'cancel them before launching new jobs with the updated version.'
            f'\n\nCurrent non-terminal jobs:\n{formatted_job_table}')

        raise ValueError(error_msg)
