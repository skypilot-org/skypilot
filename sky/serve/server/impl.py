"""Implementation of the SkyServe core APIs."""
import os
import pathlib
import re
import shlex
import signal
import tempfile
import threading
import typing
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import uuid

import colorama
import filelock

from sky import backends
from sky import clouds
from sky import exceptions
from sky import execution
from sky import global_user_state
from sky import sky_logging
from sky import skypilot_config
from sky import task as task_lib
from sky.adaptors import common as adaptors_common
from sky.backends import backend_utils
from sky.catalog import common as service_catalog_common
from sky.data import storage as storage_lib
from sky.serve import constants as serve_constants
from sky.serve import runner as serve_runner
from sky.serve import serve_rpc_utils
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.server import common as server_common
from sky.server.requests import request_names
from sky.skylet import constants
from sky.skylet import job_lib
from sky.users import permission
from sky.users import rbac
from sky.utils import admin_policy_utils
from sky.utils import command_runner
from sky.utils import common
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import dag_utils
from sky.utils import rich_utils
from sky.utils import schemas
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import ux_utils
from sky.utils import yaml_utils
from sky.workspaces import core as workspaces_core

if typing.TYPE_CHECKING:
    import grpc
else:
    grpc = adaptors_common.LazyImport('grpc')

logger = sky_logging.init_logger(__name__)


def _caller_is_admin() -> bool:
    """Whether the user behind the current request holds the admin role.

    Off the API server there is no role to check: authorization happened on
    the server before the work reached a controller, the same assumption
    the workspace permission check makes.
    """
    if os.getenv(constants.ENV_VAR_IS_SKYPILOT_SERVER) is None:
        return True
    roles = permission.permission_service.get_user_roles(
        common_utils.get_current_user().id)
    return rbac.RoleName.ADMIN.value in roles


def _names_str(service_records: List[Dict[str, Any]]) -> str:
    return ', '.join(
        repr(name) for name in sorted(
            str(record.get('name')) for record in service_records))


def _check_service_ownership(service_records: List[Dict[str, Any]],
                             pool: bool) -> None:
    """Refuses to act on a service the current user did not create.

    The services table records the creator of each service. The check
    lives here, on the API server, because one controller runs every
    user's services and has no caller to check against. Admins may act on
    any service. A record with no creator was written by a version that
    did not record one, or came from a controller that does not report
    it, so only an admin can act on it; the error says so, since to its
    creator it would otherwise read as someone else's.

    Raises:
        exceptions.PermissionDeniedError: If the caller is not an admin and
            any of the records was created by someone else.
    """
    user = common_utils.get_current_user()
    denied = [
        record for record in service_records
        if record.get('user_hash') != user.id
    ]
    if not denied or _caller_is_admin():
        return
    noun = 'pool' if pool else 'service'
    others = [r for r in denied if r.get('user_hash') is not None]
    unrecorded = [r for r in denied if r.get('user_hash') is None]
    reasons = []
    if others:
        reasons.append(f'{noun}{"s" if len(others) > 1 else ""} '
                       f'{_names_str(others)} created by another user')
    if unrecorded:
        reasons.append(
            f'{noun}{"s" if len(unrecorded) > 1 else ""} '
            f'{_names_str(unrecorded)} with no recorded creator (created by '
            'an older version, or reported by a controller that predates '
            'creator records)')
    raise exceptions.PermissionDeniedError(
        f'User {user.name} ({user.id}) cannot manage {" and ".join(reasons)}. '
        f'Only the creator of a {noun} or an admin can manage it; a {noun} '
        'with no recorded creator needs an admin.')


def _matches_service_pattern(name: str, pattern: str) -> bool:
    """Whether ``name`` is what a ``down``-style ``pattern`` means: ``*`` is
    the only wildcard, everything else is literal."""
    regex = '.*'.join(re.escape(part) for part in pattern.split('*'))
    return re.fullmatch(regex, name) is not None


def _name_reached_by(name: str, pattern: str) -> bool:
    """Whether an older controller's expansion of ``pattern`` reaches
    ``name``: it turned a name into a SQL ``LIKE`` pattern without escaping
    ``_`` and ``%``, and SQLite compares ASCII letters case-insensitively.
    """
    regex = ''.join(
        '.*' if char in '*%' else '.' if char == '_' else re.escape(char)
        for char in pattern)
    return re.fullmatch(regex, name, re.IGNORECASE) is not None


# The serve protocol version from which the controller terminates exactly
# the names it is handed (see serve_constants.SERVE_VERSION).
_EXACT_NAME_SERVE_VERSION = 6


def _controller_matches_names_exactly(
        service_records: List[Dict[str, Any]]) -> bool:
    """Whether the controller behind these records terminates exactly the
    names it is handed. Each record carries the controller's protocol
    version from version 6 on; a record without one is from an older
    controller, which expands names (see ``_name_reached_by``)."""
    return all(
        record.get('serve_version', 0) >= _EXACT_NAME_SERVE_VERSION
        for record in service_records)


def _check_names_reach_only_targets(service_names: List[str],
                                    service_records: List[Dict[str, Any]],
                                    pool: bool) -> None:
    """Refuses a name whose expansion on an older controller (see
    ``_name_reached_by``) reaches a service that was not asked for, whoever
    owns it: the controller would terminate it too, silently."""
    if _controller_matches_names_exactly(service_records):
        return
    sent = set(service_names)
    reached = [
        record for record in service_records
        if record['name'] not in sent and any(
            _name_reached_by(record['name'], name) for name in service_names)
    ]
    if not reached:
        return
    noun = 'pool' if pool else 'service'
    pairs = sorted({(name, record['name'])
                    for record in reached
                    for name in service_names
                    if _name_reached_by(record['name'], name)})
    detail = '; '.join(
        f'{name!r} also reaches {other!r}' for name, other in pairs)
    explanation = (f'Cannot terminate by name on this controller: {detail}. '
                   'The controller reads "_" as a wildcard and may match '
                   'names case-insensitively.')
    user = common_utils.get_current_user()
    others = [r for r in reached if r.get('user_hash') != user.id]
    if others and not _caller_is_admin():
        raise exceptions.PermissionDeniedError(
            f'{explanation} User {user.name} ({user.id}) cannot manage the '
            f'{noun}{"s" if len(others) > 1 else ""} reached that way.')
    with ux_utils.print_exception_no_traceback():
        raise ValueError(f'{explanation} Name every {noun} reached to '
                         'terminate them together, or upgrade the '
                         'controller.')


def _folded_service_name(service_name: str) -> str:
    """The part of a service name kept everywhere it is used: the
    controller's directory folds ``-`` to ``_``, and the cloud name folds
    ``.`` and ``_`` to ``-`` and lowercases."""
    return re.sub(r'[._-]', '-', service_name).lower()


def _service_dirs_on_controller(
        handle: backends.CloudVmRayResourceHandle) -> List[str]:
    """The per-service directory names on the controller, read over SSH."""
    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)
    returncode, stdout, stderr = backend.run_on_head(
        handle,
        f'ls -1 {serve_constants.SKYSERVE_METADATA_DIR} 2>/dev/null || true',
        require_outputs=True,
        stream_logs=False)
    if returncode != 0:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError('Could not list the service directories on the '
                               f'controller: {stderr}')
    return stdout.split()


def _check_service_name_unused(service_name: str, pool: bool) -> None:
    """Refuses a name that is taken, by its folded form, before the launch
    file-mounts this request's per-name files (TLS key, config, task YAML)
    over the existing service's; the controller's own duplicate check
    comes after that. Pools are checked against services, and the reverse,
    only when both controllers run consolidated and share the directory.
    """
    controller_type = controller_utils.get_controller_for_pool(pool)
    try:
        handle = backend_utils.is_controller_accessible(
            controller=controller_type, stopped_message='')
    except exceptions.ClusterNotUpError as e:
        # Nothing provisioned yet, or STOPPED: a stopped controller restarts
        # with every service marked CONTROLLER_FAILED, which only `down`
        # acts on. One that SSH cannot reach may run services.
        if (e.cluster_status in (None, status_lib.ClusterStatus.STOPPED) or
                getattr(e.handle, 'head_ip', None) is None):
            return
        if _caller_is_admin():
            # The launch that follows is what repairs such a controller.
            logger.warning('The controller is not reachable; not checking '
                           f'the name {service_name!r}.')
            return
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Could not check that the name {service_name!r} is free: '
                'the controller is not reachable. Try again.') from e
    assert isinstance(handle, backends.CloudVmRayResourceHandle)
    wanted = _folded_service_name(service_name)
    noun = 'pool' if pool else 'service'
    kinds = [pool]
    if (serve_utils.is_consolidation_mode(pool) and
            serve_utils.is_consolidation_mode(not pool)):
        kinds.append(not pool)
    for records_pool in kinds:
        try:
            records = serve_runner.current().get_service_status(
                handle=handle, service_names=None, pool=records_pool)
        except Exception as e:  # pylint: disable=broad-except
            # This is the only guard against the file overwrite, so a failed
            # lookup refuses the name. The one exception is a controller
            # still setting up (INIT, e.g. after an interrupted earlier up):
            # it cannot answer, and the launch that follows repairs it.
            controller_status = global_user_state.get_status_from_cluster_name(
                controller_type.value.cluster_name)
            if controller_status == status_lib.ClusterStatus.INIT:
                # Any `up` re-provisions the shared controller through INIT,
                # so INIT alone does not mean it holds no services. Check the
                # per-service directories, which are what the launch would
                # write over.
                logger.warning(
                    'Could not list the services on the controller, which is '
                    f'still setting up; checking its service directories for '
                    f'the name {service_name!r} instead: '
                    f'{common_utils.format_exception(e)}')
                for dir_name in _service_dirs_on_controller(handle):
                    if _folded_service_name(dir_name) == wanted:
                        with ux_utils.print_exception_no_traceback():
                            raise ValueError(
                                f'{noun.capitalize()} name {service_name!r} '
                                'is taken on the controller. Choose another '
                                'name.') from e
                continue
            if _caller_is_admin():
                # An older controller fails the listing on one broken
                # service, and `up` is what upgrades it; an admin may go on.
                logger.warning(
                    f'Could not list the {noun}s on the controller; not '
                    f'checking the name {service_name!r}: '
                    f'{common_utils.format_exception(e)}')
                continue
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Could not check that the {noun} name {service_name!r} '
                    'is free: listing the services on the controller failed: '
                    f'{common_utils.format_exception(e)}. Try again.') from e
        for record in records:
            if _folded_service_name(record['name']) != wanted:
                continue
            if record['name'] == service_name and records_pool == pool:
                msg = (f'{noun.capitalize()} {service_name!r} already exists. '
                       f'Choose another name, or tear the existing {noun} '
                       'down first.')
            else:
                taken_by = 'pool' if records_pool else 'service'
                msg = (f'{noun.capitalize()} name {service_name!r} is taken '
                       f'by {taken_by} {record["name"]!r}: names that differ '
                       'only in case or in ".", "-" and "_" are not told '
                       'apart everywhere a name is used. Choose another '
                       'name.')
            with ux_utils.print_exception_no_traceback():
                raise ValueError(msg)


_WITHHELD_YAML_NOTE = ('# Created by another user: only the resources '
                       'section is shown.\n')


def _schema_object_keys(schema: Dict[str, Any]) -> Optional[Set[str]]:
    """The keys an object schema (or one of its ``anyOf`` branches) names;
    None for a free-form one such as ``labels``."""
    if 'properties' in schema:
        return {k for k in schema['properties'] if not k.startswith('_')}
    for branch in schema.get('anyOf', []):
        keys = _schema_object_keys(branch)
        if keys is not None:
            return keys
    return None


def _only_keys(obj: Any, keys: Set[str]) -> Any:
    if not isinstance(obj, dict):
        return obj
    return {k: v for k, v in obj.items() if k in keys}


def _public_resources(entry: Any) -> Any:
    """``entry`` with only the public keys of the resources schema: no
    ``_``-prefixed key (a docker login, cluster config overrides), no key the
    schema does not know (``hooks``), and no legacy ``autostop.hook``
    command. The text is kept as written rather than re-parsed, which would
    reach the catalog and log deprecation warnings on every status call."""
    if not isinstance(entry, dict):
        return entry
    properties = schemas.get_resources_schema()['properties']
    public = {
        k: v
        for k, v in entry.items()
        if k in properties and not k.startswith('_')
    }
    for key, value in public.items():
        # A nested object can allow extra keys (`job_recovery` is a plugin
        # extension point), so keep only the keys its schema names.
        schema = properties[key]
        if isinstance(value, list):
            schema = schema.get('items', {})
        known = _schema_object_keys(schema)
        if known is None:
            continue
        if isinstance(value, list):
            public[key] = [_only_keys(item, known) for item in value]
        else:
            public[key] = _only_keys(value, known)
    if isinstance(public.get('autostop'), dict):
        public['autostop'] = {
            k: v
            for k, v in public['autostop'].items()
            if k not in ('hook', 'hook_timeout')
        }
    return public


def _resources_only_yaml(yaml_content: Any) -> Optional[str]:
    """The ``resources`` section of a task YAML, public keys only (see
    ``_public_resources``), and nothing else; None when there is none."""
    if not isinstance(yaml_content, str) or not yaml_content.strip():
        return None
    try:
        config = yaml_utils.safe_load(yaml_content)
    except Exception:  # pylint: disable=broad-except
        return None
    if not isinstance(config, dict):
        return None
    if not isinstance(config.get('resources'), dict):
        return None
    resources = _public_resources(config['resources'])
    for key in ('any_of', 'ordered'):
        if isinstance(resources.get(key), list):
            resources[key] = [
                _public_resources(entry) for entry in resources[key]
            ]
    return _WITHHELD_YAML_NOTE + yaml_utils.dump_yaml_str(
        {'resources': resources})


def _withhold_details_of_others(service_records: List[Dict[str, Any]]) -> None:
    """Cuts the records of services the caller did not create down to what
    another user has a use for. The task YAML carries the service's secrets
    wherever the task refers to them, so only its resources section is
    kept, which is what the batch coordinator runs jobs on another user's
    pool with; a replica's handle carries the launched resources with the
    docker login, the ssh user and the provider config; the entrypoint is
    the caller's command line, flag values and all. Admins see every record
    as it is."""
    user_id = common_utils.get_current_user().id
    others = [
        record for record in service_records
        if record.get('user_hash') != user_id
    ]
    if not others or _caller_is_admin():
        return
    for record in others:
        for key in ('yaml_content', 'pool_yaml'):
            if key in record:
                record[key] = _resources_only_yaml(record[key])
        for replica in record.get('replica_info') or []:
            if isinstance(replica, dict) and 'handle' in replica:
                replica['handle'] = None
        if 'entrypoint' in record:
            record['entrypoint'] = None


def _own_service_records(
        service_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The records created by the current user, or all of them for an admin."""
    if _caller_is_admin():
        return service_records
    user_id = common_utils.get_current_user().id
    return [
        record for record in service_records
        if record.get('user_hash') == user_id
    ]


def check_service_owned_by_caller(
        service_name: str, pool: bool,
        handle: backends.CloudVmRayResourceHandle) -> None:
    """Looks a service up on the controller and checks the caller owns it.
    A service that does not exist passes: the operation that follows reports
    it as missing, the same as before."""
    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)
    service_record = _get_service_record(service_name, pool, handle, backend)
    if service_record is not None:
        _check_service_ownership([service_record], pool)


def _rewrite_tls_credential_paths_and_get_tls_env_vars(
        service_name: str, task: 'task_lib.Task') -> Dict[str, Any]:
    """Rewrite the paths of TLS credentials in the task.

    Args:
        service_name: Name of the service.
        task: sky.Task to rewrite.

    Returns:
        The generated template variables for TLS.
    """
    service_spec = task.service
    # Already checked by validate_service_task
    assert service_spec is not None
    if service_spec.tls_credential is None:
        return {'use_tls': False}
    remote_tls_keyfile = (
        serve_utils.generate_remote_tls_keyfile_name(service_name))
    remote_tls_certfile = (
        serve_utils.generate_remote_tls_certfile_name(service_name))
    tls_template_vars = {
        'use_tls': True,
        'remote_tls_keyfile': remote_tls_keyfile,
        'remote_tls_certfile': remote_tls_certfile,
        'local_tls_keyfile': service_spec.tls_credential.keyfile,
        'local_tls_certfile': service_spec.tls_credential.certfile,
    }
    service_spec.tls_credential = serve_utils.TLSCredential(
        remote_tls_keyfile, remote_tls_certfile)
    return tls_template_vars


def _get_service_record(
        service_name: str, pool: bool,
        handle: backends.CloudVmRayResourceHandle,
        backend: backends.CloudVmRayBackend) -> Optional[Dict[str, Any]]:
    """Get the service record."""
    noun = 'pool' if pool else 'service'

    assert isinstance(handle, backends.CloudVmRayResourceHandle)
    use_legacy = not handle.is_grpc_enabled_with_flag

    if not use_legacy:
        try:
            service_statuses = serve_rpc_utils.RpcRunner.get_service_status(
                handle, [service_name], pool)
        except exceptions.SkyletMethodNotImplementedError:
            use_legacy = True

    if use_legacy:
        code = serve_utils.ServeCodeGen.get_service_status([service_name],
                                                           pool=pool)
        returncode, serve_status_payload, stderr = backend.run_on_head(
            handle,
            code,
            require_outputs=True,
            stream_logs=False,
            separate_stderr=True)
        try:
            subprocess_utils.handle_returncode(returncode,
                                               code,
                                               f'Failed to get {noun} status',
                                               stderr,
                                               stream_logs=True)
        except exceptions.CommandError as e:
            raise RuntimeError(e.error_msg) from e

        service_statuses = serve_utils.load_service_status(serve_status_payload)

    assert len(service_statuses) <= 1, service_statuses
    if not service_statuses:
        return None
    return service_statuses[0]


def _maybe_display_run_warning(task: 'task_lib.Task') -> None:
    # We do not block the user from creating a pool with a run section
    # in order to enable using the same yaml for pool creation
    # and job submission. But we want to make it clear that 'run' will not
    # be respected here.
    if task.run is not None:
        logger.warning(
            f'{colorama.Fore.YELLOW} Pool creation does not support the '
            '`run` section. Creating the pool while ignoring the '
            f'`run` section.{colorama.Style.RESET_ALL}')


def _check_storage_mounts_accessible(task: 'task_lib.Task') -> None:
    """Refuses a storage the service's controller could not reuse by name.

    The controller runs for the active workspace and reaches the storage
    of that workspace only. A storage of another workspace would be
    accepted here and synced into, then left behind at teardown. Checked
    before any bucket is made for the task.
    """
    for storage in (task.storage_mounts or {}).values():
        storage.check_accessible()


def up(
    task: 'task_lib.Task',
    service_name: Optional[str] = None,
    pool: bool = False,
    file_mounts_blob_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Spins up a service or a pool.

    Args:
        task: The task to serve.
        service_name: Name of the service or pool.
        pool: Whether this is a pool.
        file_mounts_blob_id: The blob the client uploaded the task's workdir
            and file mounts into, if any. The task's local sources point
            inside it.
    """
    task.validate()
    serve_utils.validate_service_task(task, pool=pool)
    assert task.service is not None
    assert task.service.pool == pool, 'Inconsistent pool flag.'
    noun = 'pool' if pool else 'service'
    capnoun = noun.capitalize()
    if service_name is None:
        service_name = serve_utils.generate_service_name(pool)

    # The service name will be used as:
    # 1. controller cluster name: 'sky-serve-controller-<service_name>'
    # 2. replica cluster name: '<service_name>-<replica_id>'
    # In both cases, service name shares the same regex with cluster name.
    if re.fullmatch(constants.CLUSTER_NAME_VALID_REGEX, service_name) is None:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'{capnoun} name {service_name!r} is invalid: '
                             f'ensure it is fully matched by regex (e.g., '
                             'only contains lower letters, numbers and dash): '
                             f'{constants.CLUSTER_NAME_VALID_REGEX}')
    _check_service_name_unused(service_name, pool)

    dag = dag_utils.convert_entrypoint_to_dag(task)
    # Always apply the policy again here, even though it might have been applied
    # in the CLI. This is to ensure that we apply the policy to the final DAG
    # and get the mutated config.
    dag, mutated_user_config = admin_policy_utils.apply(
        dag, request_name=request_names.AdminPolicyRequestName.SERVE_UP)
    clouds.Kubernetes.check_request_keeps_server_none()
    clouds.Kubernetes.check_task_keeps_server_none(dag.tasks[0])
    dag.resolve_and_validate_volumes()
    dag.pre_mount_volumes()
    task = dag.tasks[0]
    assert task.service is not None
    if pool:
        _maybe_display_run_warning(task)
        # Use dummy run script for pool.
        task.run = serve_constants.POOL_DUMMY_RUN_COMMAND
    _check_storage_mounts_accessible(task)

    with rich_utils.safe_status(
            ux_utils.spinner_message(f'Initializing {noun}')):
        # Handle file mounts using two-hop approach when cloud storage
        # unavailable
        storage_clouds = (
            storage_lib.get_cached_enabled_storage_cloud_names_or_refresh())
        force_disable_cloud_bucket = skypilot_config.get_nested(
            ('serve', 'force_disable_cloud_bucket'), False)
        local_to_controller_file_mounts: Dict[str, str] = {}
        # Set when the task keeps sources inside the client's upload blob.
        controller_file_mounts_blob_id: Optional[str] = None
        if storage_clouds and not force_disable_cloud_bucket:
            controller_utils.maybe_translate_local_file_mounts_and_sync_up(
                task, task_type='serve')
        else:
            # Fall back to two-hop file_mount uploading when no cloud storage
            if task.storage_mounts:
                raise exceptions.NotSupportedError(
                    'Cloud-based file_mounts are specified, but no cloud '
                    'storage is available. Please specify local '
                    'file_mounts only.')
            if serve_utils.is_consolidation_mode(pool):
                # The controller and replicas share the API server, so there
                # is no cluster hop: keep the sources as resolved from the
                # client's upload. Skip the two-hop, which would otherwise
                # stage the sources under ~/.sky/tmp/controller -- outside
                # the caller's roots, so the server would reject them.
                # Mirrors the managed jobs path in jobs/server/core.py.
                local_to_controller_file_mounts = {}
                # The resolved sources may only exist on this API server
                # replica: a blob backend can extract to a host-local cache,
                # and the controller can run, or be recovered, on another
                # replica. Hand the blob id to the controller, which passes
                # it with every replica launch so the API server re-extracts
                # the blob on whichever replica handles that launch.
                if (file_mounts_blob_id is not None and
                        server_common.local_file_mounts_required(
                            task.to_yaml_config())):
                    controller_file_mounts_blob_id = file_mounts_blob_id
            else:
                local_to_controller_file_mounts = (
                    controller_utils.translate_local_file_mounts_to_two_hop(
                        task))

    tls_template_vars = _rewrite_tls_credential_paths_and_get_tls_env_vars(
        service_name, task)

    with tempfile.NamedTemporaryFile(
            prefix=f'service-task-{service_name}-',
            mode='w',
    ) as service_file, tempfile.NamedTemporaryFile(
            prefix=f'controller-task-{service_name}-',
            mode='w',
    ) as controller_file:
        controller = controller_utils.get_controller_for_pool(pool)
        controller_name = controller.value.cluster_name
        task_config = task.to_yaml_config()
        yaml_utils.dump_yaml(service_file.name, task_config)
        remote_tmp_task_yaml_path = (
            serve_utils.generate_remote_tmp_task_yaml_file_name(service_name))
        remote_config_yaml_path = (
            serve_utils.generate_remote_config_yaml_file_name(service_name))
        controller_log_file = (
            serve_utils.generate_remote_controller_log_file_name(service_name))
        controller_resources = controller_utils.get_controller_resources(
            controller=controller, task_resources=task.resources)
        controller_job_id = None
        if serve_utils.is_consolidation_mode(pool):
            # We need a unique integer per sky.serve.up call to avoid name
            # conflict. Originally in non-consolidation mode, this is the ray
            # job id; now we use the request id hash instead. Here we also
            # make sure it is a 32-bit integer to avoid overflow on sqlalchemy.
            rid = common_utils.get_current_request_id()
            controller_job_id = hash(uuid.UUID(rid).int) & 0x7FFFFFFF

        vars_to_fill = {
            'remote_task_yaml_path': remote_tmp_task_yaml_path,
            'local_task_yaml_path': service_file.name,
            'service_name': service_name,
            'controller_log_file': controller_log_file,
            'remote_user_config_path': remote_config_yaml_path,
            'local_to_controller_file_mounts': local_to_controller_file_mounts,
            'modified_catalogs':
                service_catalog_common.get_modified_catalog_file_mounts(),
            'consolidation_mode_job_id': controller_job_id,
            'entrypoint': shlex.quote(common_utils.get_current_command()),
            # The controller may be shared among users, so the creator's user
            # hash has to be passed in explicitly rather than read from the
            # controller's own environment.
            'user_hash': shlex.quote(common_utils.get_current_user().id),
            'file_mounts_blob_id':
                (shlex.quote(controller_file_mounts_blob_id)
                 if controller_file_mounts_blob_id is not None else None),
            **tls_template_vars,
            **controller_utils.shared_controller_vars_to_fill(
                controller=controller_utils.Controllers.SKY_SERVE_CONTROLLER,
                remote_user_config_path=remote_config_yaml_path,
                local_user_config=mutated_user_config,
            ),
        }
        common_utils.fill_template(serve_constants.CONTROLLER_TEMPLATE,
                                   vars_to_fill,
                                   output_path=controller_file.name)
        controller_task = task_lib.Task.from_yaml(controller_file.name)
        # TODO(tian): Probably run another sky.launch after we get the load
        # balancer port from the controller? So we don't need to open so many
        # ports here. Or, we should have a nginx traffic control to refuse
        # any connection to the unregistered ports.
        if not pool:
            controller_resources = {
                r.copy(ports=[serve_constants.LOAD_BALANCER_PORT_RANGE])
                for r in controller_resources
            }
        controller_task.set_resources(controller_resources)

        # # Set service_name so the backend will know to modify default ray
        # task CPU usage to custom value instead of default 0.5 vCPU. We need
        # to set it to a smaller value to support a larger number of services.
        controller_task.service_name = service_name

        # We directly submit the request to the controller and let the
        # controller to check name conflict. Suppose we have multiple
        # sky.serve.up() with same service name, the first one will
        # successfully write its job id to controller service database;
        # and for all following sky.serve.up(), the controller will throw
        # an exception (name conflict detected) and exit. Therefore the
        # controller job id in database could be use as an indicator of
        # whether the service is already running. If the id is the same
        # with the current job id, we know the service is up and running
        # for the first time; otherwise it is a name conflict.
        # Since the controller may be shared among multiple users, launch the
        # controller with the API server's user hash.
        if not serve_utils.is_consolidation_mode(pool):
            print(f'{colorama.Fore.YELLOW}Launching controller for '
                  f'{service_name!r}...{colorama.Style.RESET_ALL}')
            with common.with_server_user(
            ), skypilot_config.local_active_workspace_ctx(
                    constants.SKYPILOT_DEFAULT_WORKSPACE
            ), (
                    # Serve controller is not placed in kueue, as the controller
                    # pod is considered a "system" pod and is not subject to
                    # queue limits or preemption.
                    skypilot_config.remove_queue_name_from_config()):
                controller_job_id, controller_handle = execution.launch(
                    task=controller_task,
                    cluster_name=controller_name,
                    retry_until_up=True,
                    _request_name=request_names.AdminPolicyRequestName.
                    SERVE_LAUNCH_CONTROLLER,
                    _disable_controller_check=True,
                )
        else:
            controller_type = controller_utils.get_controller_for_pool(pool)
            controller_handle = backend_utils.is_controller_accessible(
                controller=controller_type, stopped_message='')
            backend = backend_utils.get_backend_from_handle(controller_handle)
            assert isinstance(backend, backends.CloudVmRayBackend)
            backend.sync_file_mounts(
                handle=controller_handle,
                all_file_mounts=controller_task.file_mounts,
                storage_mounts=controller_task.storage_mounts)
            run_script = controller_task.run
            assert isinstance(run_script, str)
            # Manually add the env variables to the run script. Originally
            # this is done in ray jobs submission but now we have to do it
            # manually because there is no ray runtime on the API server.
            env_cmds = [
                f'export {k}={v!r}' for k, v in controller_task.envs.items()
            ]
            run_script = '\n'.join(env_cmds + [run_script])
            # Dump script for high availability recovery.
            serve_state.set_ha_recovery_script(service_name, run_script)
            self_pod_ip_dbg = os.environ.get('POD_IP', '<unset>')
            logger.debug(f'Serve up() run_on_head: spawning controller '
                         f'subprocess locally on {self_pod_ip_dbg}')
            # LocalProcessCommandRunner (used for consolidation-mode spawns)
            # supplies a clean server env to the subprocess so per-request
            # env pollution doesn't leak into the long-lived serve
            # controller. See LocalProcessCommandRunner.run for details.
            backend.run_on_head(controller_handle, run_script)

        style = colorama.Style
        fore = colorama.Fore

        assert controller_job_id is not None and controller_handle is not None
        assert isinstance(controller_handle, backends.CloudVmRayResourceHandle)
        backend = backend_utils.get_backend_from_handle(controller_handle)
        assert isinstance(backend, backends.CloudVmRayBackend)
        # TODO(tian): Cache endpoint locally to speedup. Endpoint won't
        # change after the first time, so there is no consistency issue.
        try:
            with rich_utils.safe_status(
                    ux_utils.spinner_message(
                        f'Waiting for the {noun} to register')):
                # This function will check the controller job id in the database
                # and return the endpoint if the job id matches. Otherwise it
                # will return None.
                use_legacy = not controller_handle.is_grpc_enabled_with_flag

                if controller_handle.is_grpc_enabled_with_flag:
                    try:
                        lb_port = serve_rpc_utils.RpcRunner.wait_service_registration(  # pylint: disable=line-too-long
                            controller_handle, service_name, controller_job_id,
                            pool)
                    except exceptions.SkyletMethodNotImplementedError:
                        use_legacy = True

                if use_legacy:
                    code = serve_utils.ServeCodeGen.wait_service_registration(
                        service_name, controller_job_id, pool)
                    returncode, lb_port_payload, _ = backend.run_on_head(
                        controller_handle,
                        code,
                        require_outputs=True,
                        stream_logs=False)
                    subprocess_utils.handle_returncode(
                        returncode, code,
                        f'Failed to wait for {noun} initialization',
                        lb_port_payload)
                    lb_port = serve_utils.load_service_initialization_result(
                        lb_port_payload)
        except (exceptions.CommandError, grpc.FutureTimeoutError,
                grpc.RpcError):
            if serve_utils.is_consolidation_mode(pool):
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        f'Failed to wait for {noun} initialization. '
                        'Please check the logs above for more details.'
                    ) from None
            statuses = backend.get_job_status(controller_handle,
                                              [controller_job_id],
                                              stream_logs=False)
            controller_job_status = list(statuses.values())[0]
            if controller_job_status == job_lib.JobStatus.PENDING:
                # Max number of services reached due to vCPU constraint.
                # The controller job is pending due to ray job scheduling.
                # We manually cancel the job here.
                backend.cancel_jobs(controller_handle, [controller_job_id])
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        controller_utils.get_max_services_error_message(
                            pool)) from None
            else:
                # Possible cases:
                # (1) name conflict;
                # (2) max number of services reached due to memory
                # constraint. The job will successfully run on the
                # controller, but there will be an error thrown due
                # to memory constraint check in the controller.
                # See sky/serve/service.py for more details.
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        'Failed to spin up the service. Please '
                        'check the logs above for more details.') from None
        else:
            if not serve_utils.is_consolidation_mode(pool) and not pool:
                socket_endpoint = backend_utils.get_endpoints(
                    controller_handle.cluster_name,
                    lb_port,
                    skip_status_check=True).get(lb_port)
            else:
                socket_endpoint = f'localhost:{lb_port}'
            assert socket_endpoint is not None, (
                'Did not get endpoint for controller.')
            # Already checked by validate_service_task
            assert task.service is not None
            protocol = ('http'
                        if task.service.tls_credential is None else 'https')
            socket_endpoint = socket_endpoint.replace('https://', '').replace(
                'http://', '')
            endpoint = f'{protocol}://{socket_endpoint}'

        if pool:
            logger.info(
                f'{fore.CYAN}Pool name: '
                f'{style.BRIGHT}{service_name}{style.RESET_ALL}'
                f'\n📋 Useful Commands'
                f'\n{ux_utils.INDENT_SYMBOL}To submit jobs to the pool:\t\t'
                f'{ux_utils.BOLD}sky jobs launch --pool {service_name} '
                f'<yaml_file>{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To submit multiple jobs:\t\t'
                f'{ux_utils.BOLD}sky jobs launch --pool {service_name} '
                f'--num-jobs 10 <yaml_file>{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To check the pool status:\t\t'
                f'{ux_utils.BOLD}sky jobs pool status {service_name}'
                f'{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To terminate the pool:\t\t'
                f'{ux_utils.BOLD}sky jobs pool down {service_name}'
                f'{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_LAST_SYMBOL}'
                'To update the number of workers:\t'
                f'{ux_utils.BOLD}sky jobs pool apply --pool {service_name} '
                f'--workers 5{ux_utils.RESET_BOLD}'
                '\n\n' + ux_utils.finishing_message('Successfully created pool '
                                                    f'{service_name!r}.'))
        else:
            logger.info(
                f'{fore.CYAN}Service name: '
                f'{style.BRIGHT}{service_name}{style.RESET_ALL}'
                f'\n{fore.CYAN}Endpoint URL: '
                f'{style.BRIGHT}{endpoint}{style.RESET_ALL}'
                f'\n📋 Useful Commands'
                f'\n{ux_utils.INDENT_SYMBOL}To check service status:\t'
                f'{ux_utils.BOLD}sky serve status {service_name} '
                f'[--endpoint]{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To teardown the service:\t'
                f'{ux_utils.BOLD}sky serve down {service_name}'
                f'{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To see replica logs:\t'
                f'{ux_utils.BOLD}sky serve logs {service_name} [REPLICA_ID]'
                f'{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To see load balancer logs:\t'
                f'{ux_utils.BOLD}sky serve logs --load-balancer {service_name}'
                f'{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To see controller logs:\t'
                f'{ux_utils.BOLD}sky serve logs --controller {service_name}'
                f'{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_SYMBOL}To monitor the status:\t'
                f'{ux_utils.BOLD}watch -n10 sky serve status {service_name}'
                f'{ux_utils.RESET_BOLD}'
                f'\n{ux_utils.INDENT_LAST_SYMBOL}To send a test request:\t'
                f'{ux_utils.BOLD}curl {endpoint}'
                f'{ux_utils.RESET_BOLD}'
                '\n\n' + ux_utils.finishing_message(
                    'Service is spinning up and replicas '
                    'will be ready shortly.'))
        return service_name, endpoint


def update(
    task: Optional['task_lib.Task'],
    service_name: str,
    mode: serve_utils.UpdateMode = serve_utils.DEFAULT_UPDATE_MODE,
    pool: bool = False,
    workers: Optional[int] = None,
) -> None:
    """Updates an existing service or pool."""
    noun = 'pool' if pool else 'service'
    capnoun = noun.capitalize()

    controller_type = controller_utils.get_controller_for_pool(pool)
    handle = backend_utils.is_controller_accessible(
        controller=controller_type,
        stopped_message=
        'Service controller is stopped. There is no service to update. '
        f'To spin up a new service, use {ux_utils.BOLD}'
        f'sky serve up{ux_utils.RESET_BOLD}',
        non_existent_message='Service does not exist. '
        'To spin up a new service, '
        f'use {ux_utils.BOLD}sky serve up{ux_utils.RESET_BOLD}',
    )

    assert isinstance(handle, backends.CloudVmRayResourceHandle)
    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend)

    service_record = _get_service_record(service_name, pool, handle, backend)

    if service_record is None:
        cmd = 'sky jobs pool up' if pool else 'sky serve up'
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Cannot find {noun} {service_name!r}.'
                               f'To spin up a {noun}, use {ux_utils.BOLD}'
                               f'{cmd}{ux_utils.RESET_BOLD}')
    _check_service_ownership([service_record], pool)

    # An update with a task records its storage, the new version's file
    # mounts bucket included, in the active workspace, and the controller
    # tears down only the storage of the workspace it was launched in. A
    # controller that cannot say which (an older one, or its config lost
    # to a pod restart) is not held to this.
    service_workspace = service_record.get('workspace')
    if task is not None and service_workspace is not None:
        service_workspace = skypilot_config.resolve_workspace_ref(
            service_workspace)
        active_workspace = skypilot_config.get_active_workspace()
        if service_workspace != active_workspace:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'{capnoun} {service_name!r} runs in workspace '
                    f'{skypilot_config.get_workspace_name(service_workspace)!r}'
                    ' and the active workspace is '
                    f'{skypilot_config.get_workspace_name(active_workspace)!r}'
                    f'. The {noun} reuses and tears down the storage of its '
                    'own workspace only, so update it from that workspace.')

    # If task is None and workers is specified, load existing configuration
    # and update replica count.
    if task is None:
        if workers is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Cannot update {noun} without specifying '
                    f'task or workers. Please provide either a task '
                    f'or specify the number of workers.')

        if not pool:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Non-pool service, trying to update replicas to '
                    f'{workers} is not supported. Ignoring the update.')

        # Load the existing task configuration from the service's YAML file
        yaml_content = service_record['yaml_content']

        # Load the existing task configuration
        task = task_lib.Task.from_yaml_str(yaml_content)

        if task.service is None:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('No service configuration found in '
                                   f'existing {noun} {service_name!r}')
        task.set_service(task.service.copy(min_replicas=workers))

    task.validate()
    serve_utils.validate_service_task(task, pool=pool)

    # Now apply the policy and handle task-specific logic
    # Always apply the policy again here, even though it might have been applied
    # in the CLI. This is to ensure that we apply the policy to the final DAG
    # and get the mutated config.
    # TODO(cblmemo,zhwu): If a user sets a new skypilot_config, the update
    # will not apply the config.
    dag, _ = admin_policy_utils.apply(
        task, request_name=request_names.AdminPolicyRequestName.SERVE_UPDATE)
    task = dag.tasks[0]
    clouds.Kubernetes.check_request_keeps_server_none()
    clouds.Kubernetes.check_task_keeps_server_none(task)
    if pool:
        _maybe_display_run_warning(task)
        # Use dummy run script for pool.
        task.run = serve_constants.POOL_DUMMY_RUN_COMMAND

    assert task.service is not None
    if not pool and task.service.tls_credential is not None:
        logger.warning('Updating TLS keyfile and certfile is not supported. '
                       'Any updates to the keyfile and certfile will not take '
                       'effect. To update TLS keyfile and certfile, please '
                       'tear down the service and spin up a new one.')

    prompt = None
    if (service_record['status'] == serve_state.ServiceStatus.CONTROLLER_FAILED
       ):
        prompt = (f'{capnoun} {service_name!r} has a failed controller. '
                  f'Please clean up the {noun} and try again.')
    elif (service_record['status'] == serve_state.ServiceStatus.CONTROLLER_INIT
         ):
        prompt = (f'{capnoun} {service_name!r} is still initializing '
                  'its controller. Please try again later.')
    if prompt is not None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(prompt)

    if not pool:
        original_lb_policy = service_record['load_balancing_policy']
        assert task.service is not None, 'Service section not found.'
        if original_lb_policy != task.service.load_balancing_policy:
            logger.warning(
                f'{colorama.Fore.YELLOW}Current load balancing policy '
                f'{original_lb_policy!r} is different from the new policy '
                f'{task.service.load_balancing_policy!r}. Updating the load '
                'balancing policy is not supported yet and it will be ignored. '
                'The service will continue to use the current load balancing '
                f'policy.{colorama.Style.RESET_ALL}')

    _check_storage_mounts_accessible(task)
    with rich_utils.safe_status(
            ux_utils.spinner_message(f'Initializing {noun}')):
        controller_utils.maybe_translate_local_file_mounts_and_sync_up(
            task, task_type='serve')

    use_legacy = not handle.is_grpc_enabled_with_flag

    if not use_legacy:
        try:
            current_version = serve_rpc_utils.RpcRunner.add_version(
                handle, service_name)
        except exceptions.SkyletMethodNotImplementedError:
            use_legacy = True

    if use_legacy:
        code = serve_utils.ServeCodeGen.add_version(service_name)
        returncode, version_string_payload, stderr = backend.run_on_head(
            handle,
            code,
            require_outputs=True,
            stream_logs=False,
            separate_stderr=True)
        try:
            subprocess_utils.handle_returncode(returncode,
                                               code,
                                               'Failed to add version',
                                               stderr,
                                               stream_logs=True)
        except exceptions.CommandError as e:
            raise RuntimeError(e.error_msg) from e

        version_string = serve_utils.load_version_string(version_string_payload)
        try:
            current_version = int(version_string)
        except ValueError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Failed to parse version: {version_string}; '
                                 f'Returncode: {returncode}') from e

    if not pool:
        # Recovery must use the installed files, not temporary API upload paths.
        task.service.tls_credential = (serve_utils.TLSCredential(
            serve_utils.generate_remote_tls_keyfile_name(service_name),
            serve_utils.generate_remote_tls_certfile_name(service_name))
                                       if service_record['tls_encrypted'] else
                                       None)

    with tempfile.NamedTemporaryFile(
            prefix=f'{service_name}-v{current_version}',
            mode='w') as service_file:
        task_config = task.to_yaml_config()
        yaml_utils.dump_yaml(service_file.name, task_config)
        remote_task_yaml_path = serve_utils.generate_task_yaml_file_name(
            service_name, current_version, expand_user=False)

        with sky_logging.silent():
            backend.sync_file_mounts(handle,
                                     {remote_task_yaml_path: service_file.name},
                                     storage_mounts=None)

        use_legacy = not handle.is_grpc_enabled_with_flag

        if not use_legacy:
            try:
                serve_rpc_utils.RpcRunner.update_service(
                    handle, service_name, current_version, mode, pool)
            except exceptions.SkyletMethodNotImplementedError:
                use_legacy = True

        if use_legacy:
            code = serve_utils.ServeCodeGen.update_service(service_name,
                                                           current_version,
                                                           mode=mode.value,
                                                           pool=pool)
            returncode, _, stderr = backend.run_on_head(handle,
                                                        code,
                                                        require_outputs=True,
                                                        stream_logs=False,
                                                        separate_stderr=True)
            try:
                subprocess_utils.handle_returncode(returncode,
                                                   code,
                                                   f'Failed to update {noun}s',
                                                   stderr,
                                                   stream_logs=True)
            except exceptions.CommandError as e:
                raise RuntimeError(e.error_msg) from e

    cmd = 'sky jobs pool status' if pool else 'sky serve status'
    logger.info(
        f'{colorama.Fore.GREEN}{capnoun} {service_name!r} update scheduled.'
        f'{colorama.Style.RESET_ALL}\n'
        f'Please use {ux_utils.BOLD}{cmd} {service_name} '
        f'{ux_utils.RESET_BOLD}to check the latest status.')

    if pool:
        logs_cmd = f'`sky jobs pool logs {service_name} <worker_id>`'
        unit_noun = 'Workers'

    else:
        logs_cmd = f'`sky serve logs {service_name} <replica_id>`'
        unit_noun = 'Replicas'
    logger.info(
        ux_utils.finishing_message(
            f'Successfully updated {noun} {service_name!r} '
            f'to version {current_version}.',
            follow_up_message=
            f'\n{unit_noun} are updating, use {ux_utils.BOLD}{logs_cmd}'
            f'{ux_utils.RESET_BOLD} to check their status.'))


def apply(
    task: 'task_lib.Task',
    workers: Optional[int],
    service_name: str,
    mode: serve_utils.UpdateMode = serve_utils.DEFAULT_UPDATE_MODE,
    pool: bool = False,
    file_mounts_blob_id: Optional[str] = None,
) -> None:
    """Applies the config to the service or pool."""
    with filelock.FileLock(serve_utils.get_service_filelock_path(service_name)):
        try:
            controller_type = controller_utils.get_controller_for_pool(pool)
            handle = backend_utils.is_controller_accessible(
                controller=controller_type, stopped_message='')
            backend = backend_utils.get_backend_from_handle(handle)
            assert isinstance(backend, backends.CloudVmRayBackend)
            service_record = _get_service_record(service_name, pool, handle,
                                                 backend)
            if service_record is not None:
                # Refuse update for terminal-state rows (CONTROLLER_FAILED /
                # FAILED_CLEANUP / SHUTTING_DOWN). The controller HTTP
                # listener may already be gone, so update would just hit
                # ECONNREFUSED with a confusing error. SHUTTING_DOWN is the
                # ambiguous case: it can be a normal in-progress shutdown
                # (will self-resolve in seconds) or a zombie (cleanup died
                # mid-flight; recoverable only via --purge). We give it a
                # friendlier message so a user who just ran `sky serve down`
                # and immediately re-applies isn't pushed to --purge.
                svc_status = service_record['status']
                if svc_status in (
                        serve_state.ServiceStatus.terminal_statuses()):
                    noun = 'pool' if pool else 'service'
                    purge_cmd = (f'sky jobs pool down {service_name} --purge'
                                 if pool else
                                 f'sky serve down {service_name} --purge')
                    if svc_status == serve_state.ServiceStatus.SHUTTING_DOWN:
                        msg = (f'{noun.capitalize()} {service_name!r} is '
                               f'shutting down. Wait for shutdown to '
                               f'complete, then re-apply. If it stays in '
                               f'this state for a long time, the cleanup '
                               f'may be stuck; run `{purge_cmd}` to '
                               f'force-clean.')
                    else:
                        msg = (f'{noun.capitalize()} {service_name!r} is '
                               f'in {svc_status.value} state and cannot '
                               f'be updated. Run `{purge_cmd}` to clean '
                               f'it up and retry.')
                    with ux_utils.print_exception_no_traceback():
                        raise RuntimeError(msg)
                return update(task, service_name, mode, pool, workers)
        except exceptions.ClusterNotUpError:
            pass
        up(task, service_name, pool, file_mounts_blob_id=file_mounts_blob_id)


def down(
    service_names: Optional[Union[str, List[str]]] = None,
    all: bool = False,  # pylint: disable=redefined-builtin
    purge: bool = False,
    pool: bool = False,
) -> None:
    """Tears down a service or pool."""
    noun = 'pool' if pool else 'service'
    if service_names is None:
        service_names = []
    if isinstance(service_names, str):
        service_names = [service_names]
    controller_type = controller_utils.get_controller_for_pool(pool)
    handle = backend_utils.is_controller_accessible(
        controller=controller_type,
        stopped_message=f'All {noun}s should have terminated.')

    service_names_str = ','.join(service_names)
    if sum([bool(service_names), all]) != 1:
        argument_str = (f'{noun}_names={service_names_str}'
                        if service_names else '')
        argument_str += ' all' if all else ''
        raise ValueError(f'Can only specify one of {noun}_names or all. '
                         f'Provided {argument_str!r}.')

    assert isinstance(handle, backends.CloudVmRayResourceHandle)

    try:
        # Resolve the request to concrete records first, so each can be
        # checked against its creator. Every record is fetched: an older
        # controller expands each name it is handed (see _name_reached_by),
        # so what a request reaches is decided against the whole table.
        service_records: Optional[List[Dict[str, Any]]]
        try:
            service_records = serve_runner.current().get_service_status(
                handle=handle, service_names=None, pool=pool)
        except Exception as e:  # pylint: disable=broad-except
            # An older controller fails the listing on one broken service.
            # An admin may still tear down by exact name, as before.
            if all or not _caller_is_admin():
                raise
            logger.warning(
                f'Could not list the {noun}s on the controller; handing it '
                f'{service_names_str} unchecked (an older controller also '
                'matches names differing in case or "_"): '
                f'{common_utils.format_exception(e)}')
            service_records = None
        if service_records is not None:
            if all:
                # `down --all` from a non-admin means all of the caller's own.
                targets = _own_service_records(service_records)
                skipped = len(service_records) - len(targets)
                if skipped:
                    logger.info(
                        f'Skipped {skipped} {noun}{"s" if skipped > 1 else ""} '
                        'not owned by you.')
            else:
                targets = [
                    record for record in service_records if any(
                        _matches_service_pattern(record['name'], pattern)
                        for pattern in service_names)
                ]
                _check_service_ownership(targets, pool)
            service_names = [record['name'] for record in targets]
            if not service_names:
                # Decided here rather than by handing the controller an empty
                # list, which it must not mistake for "every service".
                logger.info(f'No {noun} to terminate.')
                return
            # The controller expands even an exact name; refuse one that would
            # reach a service that was not asked for.
            _check_names_reach_only_targets(service_names, service_records,
                                            pool)

        use_legacy = not handle.is_grpc_enabled_with_flag

        if not use_legacy:
            try:
                stdout = serve_rpc_utils.RpcRunner.terminate_services(
                    handle, service_names, purge, pool)
            except exceptions.SkyletMethodNotImplementedError:
                use_legacy = True

        if use_legacy:
            backend = backend_utils.get_backend_from_handle(handle)
            assert isinstance(backend, backends.CloudVmRayBackend)
            code = serve_utils.ServeCodeGen.terminate_services(
                service_names, purge, pool)

            returncode, stdout, _ = backend.run_on_head(handle,
                                                        code,
                                                        require_outputs=True,
                                                        stream_logs=False)

            subprocess_utils.handle_returncode(returncode, code,
                                               f'Failed to terminate {noun}',
                                               stdout)
    except exceptions.FetchClusterInfoError as e:
        raise RuntimeError(
            'Failed to fetch controller IP. Please refresh controller status '
            f'by `sky status -r {controller_type.value.cluster_name}` and try '
            'again.') from e
    except exceptions.CommandError as e:
        raise RuntimeError(e.error_msg) from e
    except grpc.RpcError as e:
        raise RuntimeError(f'{e.details()} ({e.code()})') from e
    except grpc.FutureTimeoutError as e:
        raise RuntimeError('gRPC timed out') from e

    logger.info(stdout)


class _DefaultServiceStatusRunner:
    """Default implementation — gRPC with codegen + run_on_head fallback.

    Registered lazily by ``sky.serve.runner.current()``. Plugins override
    by calling ``sky.serve.runner.register()`` with their own
    implementation.
    """

    def get_service_status(
        self,
        *,
        handle: 'backends.CloudVmRayResourceHandle',
        service_names: Optional[List[str]],
        pool: bool,
    ) -> List[Dict[str, Any]]:
        noun = 'pool' if pool else 'service'
        use_legacy = not handle.is_grpc_enabled_with_flag

        service_records: List[Dict[str, Any]] = []
        if not use_legacy:
            try:
                service_records = serve_rpc_utils.RpcRunner.get_service_status(
                    handle, service_names, pool)
            except exceptions.SkyletMethodNotImplementedError:
                use_legacy = True

        if use_legacy:
            backend = backend_utils.get_backend_from_handle(handle)
            assert isinstance(backend, backends.CloudVmRayBackend)

            code = serve_utils.ServeCodeGen.get_service_status(service_names,
                                                               pool=pool)
            returncode, serve_status_payload, stderr = backend.run_on_head(
                handle,
                code,
                require_outputs=True,
                stream_logs=False,
                separate_stderr=True)

            try:
                subprocess_utils.handle_returncode(returncode,
                                                   code,
                                                   f'Failed to fetch {noun}s',
                                                   stderr,
                                                   stream_logs=True)
            except exceptions.CommandError as e:
                raise RuntimeError(e.error_msg) from e

            service_records = serve_utils.load_service_status(
                serve_status_payload)

        return service_records


def _resolve_user_names(service_records: List[Dict[str, Any]]) -> None:
    """Fills in the ``user_name`` of each record from its ``user_hash``.

    The user table lives on the API server, while the records come from the
    controller, so the display name is resolved here in one batched query.
    Records created before the creator was recorded have no ``user_hash``; they
    are left with ``user_name`` unset.
    """
    user_hashes = {
        record['user_hash']
        for record in service_records
        if record.get('user_hash') is not None
    }
    if not user_hashes:
        return
    users = global_user_state.get_users(user_hashes)
    for record in service_records:
        user = users.get(record.get('user_hash'))
        if user is not None:
            record['user_name'] = user.name


def status(
    service_names: Optional[Union[str, List[str]]] = None,
    pool: bool = False,
) -> List[Dict[str, Any]]:
    """Gets statuses of services or pools."""
    noun = 'pool' if pool else 'service'
    if service_names is not None:
        if isinstance(service_names, str):
            service_names = [service_names]

    try:
        backend_utils.check_network_connection()
    except exceptions.NetworkError as e:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Failed to refresh {noun}s status '
                               'due to network error.') from e

    controller_type = controller_utils.get_controller_for_pool(pool)
    handle = backend_utils.is_controller_accessible(
        controller=controller_type,
        stopped_message=controller_type.value.default_hint_if_non_existent.
        replace('service', noun))

    assert isinstance(handle, backends.CloudVmRayResourceHandle)

    service_records = serve_runner.current().get_service_status(
        handle=handle, service_names=service_names, pool=pool)

    # Get the endpoint for each service
    for service_record in service_records:
        service_record['endpoint'] = None
        # Pool doesn't have an endpoint.
        if pool:
            continue
        if service_record['load_balancer_port'] is not None:
            try:
                lb_port = service_record['load_balancer_port']
                if not serve_utils.is_consolidation_mode(pool):
                    endpoint = backend_utils.get_endpoints(
                        cluster=common.SKY_SERVE_CONTROLLER_NAME,
                        port=lb_port).get(lb_port, None)
                else:
                    endpoint = f'localhost:{lb_port}'
            except exceptions.ClusterNotUpError:
                pass
            else:
                protocol = ('https'
                            if service_record['tls_encrypted'] else 'http')
                if endpoint is not None:
                    endpoint = endpoint.replace('https://',
                                                '').replace('http://', '')
                service_record['endpoint'] = f'{protocol}://{endpoint}'

    _withhold_workspaces_of_unreadable(service_records)
    _resolve_user_names(service_records)
    _withhold_details_of_others(service_records)

    return service_records


def _withhold_workspaces_of_unreadable(
        service_records: List[Dict[str, Any]]) -> None:
    """Drops the workspace from records in workspaces the caller cannot
    read; status lists every user's services."""
    readable = workspaces_core.get_readable_workspace_names()
    if readable is None:
        return
    for record in service_records:
        if record.get('workspace') not in readable:
            record['workspace'] = None


ServiceComponentOrStr = Union[str, serve_utils.ServiceComponent]


def tail_logs(
    service_name: str,
    *,
    target: ServiceComponentOrStr,
    replica_id: Optional[int] = None,
    follow: bool = True,
    tail: Optional[int] = None,
    pool: bool = False,
) -> None:
    """Tail logs of a service or pool."""
    if isinstance(target, str):
        target = serve_utils.ServiceComponent(target)

    if pool and target == serve_utils.ServiceComponent.LOAD_BALANCER:
        raise ValueError(f'Target {target} is not supported for pool.')

    if target == serve_utils.ServiceComponent.REPLICA:
        if replica_id is None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    '`replica_id` must be specified when using target=REPLICA.')
    else:
        if replica_id is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError('`replica_id` must be None when using '
                                 'target=CONTROLLER/LOAD_BALANCER.')

    controller_type = controller_utils.get_controller_for_pool(pool)
    handle = backend_utils.is_controller_accessible(
        controller=controller_type,
        stopped_message=controller_type.value.default_hint_if_non_existent)

    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend), backend

    # Logs carry the service's output; only its creator or an admin reads
    # them, checked before anything streams.
    check_service_owned_by_caller(service_name, pool, handle)

    if target != serve_utils.ServiceComponent.REPLICA:
        code = serve_utils.ServeCodeGen.stream_serve_process_logs(
            service_name,
            stream_controller=(
                target == serve_utils.ServiceComponent.CONTROLLER),
            follow=follow,
            tail=tail,
            pool=pool)
    else:
        assert replica_id is not None, service_name
        code = serve_utils.ServeCodeGen.stream_replica_logs(service_name,
                                                            replica_id,
                                                            follow,
                                                            tail=tail,
                                                            pool=pool)

    # With the stdin=subprocess.DEVNULL, the ctrl-c will not directly
    # kill the process, so we need to handle it manually here.
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, backend_utils.interrupt_handler)
        signal.signal(signal.SIGTSTP, backend_utils.stop_handler)

    # Refer to the notes in
    # sky/backends/cloud_vm_ray_backend.py::CloudVmRayBackend::tail_logs.
    backend.run_on_head(handle,
                        code,
                        stream_logs=True,
                        process_stream=False,
                        ssh_mode=command_runner.SshMode.INTERACTIVE)


def _get_all_replica_targets(
        service_name: str, backend: backends.CloudVmRayBackend,
        handle: backends.CloudVmRayResourceHandle,
        pool: bool) -> Set[serve_utils.ServiceComponentTarget]:
    """Helper function to get targets for all live replicas."""
    assert isinstance(handle, backends.CloudVmRayResourceHandle)
    use_legacy = not handle.is_grpc_enabled_with_flag

    if not use_legacy:
        try:
            service_records = serve_rpc_utils.RpcRunner.get_service_status(
                handle, [service_name], pool)
        except exceptions.SkyletMethodNotImplementedError:
            use_legacy = True

    if use_legacy:
        code = serve_utils.ServeCodeGen.get_service_status([service_name],
                                                           pool=pool)
        returncode, serve_status_payload, stderr = backend.run_on_head(
            handle,
            code,
            require_outputs=True,
            stream_logs=False,
            separate_stderr=True)

        try:
            subprocess_utils.handle_returncode(returncode,
                                               code,
                                               'Failed to fetch services',
                                               stderr,
                                               stream_logs=True)
        except exceptions.CommandError as e:
            raise RuntimeError(e.error_msg) from e

        service_records = serve_utils.load_service_status(serve_status_payload)

    if not service_records:
        raise ValueError(f'Service {service_name!r} not found.')
    assert len(service_records) == 1
    service_record = service_records[0]

    return {
        serve_utils.ServiceComponentTarget(serve_utils.ServiceComponent.REPLICA,
                                           replica_info['replica_id'])
        for replica_info in service_record['replica_info']
    }


def sync_down_logs(
    service_name: str,
    *,
    local_dir: str,
    targets: Union[ServiceComponentOrStr, List[ServiceComponentOrStr],
                   None] = None,
    replica_ids: Optional[List[int]] = None,
    tail: Optional[int] = None,
    pool: bool = False,
) -> str:
    """Sync down logs of a service or pool."""
    noun = 'pool' if pool else 'service'
    repnoun = 'worker' if pool else 'replica'
    caprepnoun = repnoun.capitalize()

    # Step 0) get the controller handle
    with rich_utils.safe_status(
            ux_utils.spinner_message(f'Checking {noun} status...')):
        controller_type = controller_utils.get_controller_for_pool(pool)
        handle = backend_utils.is_controller_accessible(
            controller=controller_type,
            stopped_message=controller_type.value.default_hint_if_non_existent)
        backend: backends.CloudVmRayBackend = (
            backend_utils.get_backend_from_handle(handle))
        # Same rule as tail_logs: the creator or an admin, checked first.
        check_service_owned_by_caller(service_name, pool, handle)

    requested_components: Set[serve_utils.ServiceComponent] = set()
    if not targets:
        # No targets specified -> request all components
        requested_components = {
            serve_utils.ServiceComponent.CONTROLLER,
            serve_utils.ServiceComponent.LOAD_BALANCER,
            serve_utils.ServiceComponent.REPLICA
        }
    else:
        # Parse provided targets
        if isinstance(targets, (str, serve_utils.ServiceComponent)):
            requested_components = {serve_utils.ServiceComponent(targets)}
        else:  # list
            requested_components = {
                serve_utils.ServiceComponent(t) for t in targets
            }

    normalized_targets: Set[serve_utils.ServiceComponentTarget] = set()
    if serve_utils.ServiceComponent.CONTROLLER in requested_components:
        normalized_targets.add(
            serve_utils.ServiceComponentTarget(
                serve_utils.ServiceComponent.CONTROLLER))
    if serve_utils.ServiceComponent.LOAD_BALANCER in requested_components:
        normalized_targets.add(
            serve_utils.ServiceComponentTarget(
                serve_utils.ServiceComponent.LOAD_BALANCER))
    if serve_utils.ServiceComponent.REPLICA in requested_components:
        with rich_utils.safe_status(
                ux_utils.spinner_message(f'Getting live {repnoun} infos...')):
            replica_targets = _get_all_replica_targets(service_name, backend,
                                                       handle, pool)
        if not replica_ids:
            # Replica target requested but no specific IDs
            # -> Get all replica logs
            normalized_targets.update(replica_targets)
        else:
            # Replica target requested with specific IDs
            requested_replica_targets = [
                serve_utils.ServiceComponentTarget(
                    serve_utils.ServiceComponent.REPLICA, rid)
                for rid in replica_ids
            ]
            for target in requested_replica_targets:
                if target not in replica_targets:
                    logger.warning(f'{caprepnoun} ID {target.replica_id} not '
                                   f'found for {service_name}. Skipping...')
                else:
                    normalized_targets.add(target)

    def sync_down_logs_by_target(target: serve_utils.ServiceComponentTarget):
        component = target.component
        # We need to set one side of the pipe to a logs stream, and the other
        # side to a file.
        log_path = str(pathlib.Path(local_dir) / f'{target}.log')
        stream_logs_code: str

        if component == serve_utils.ServiceComponent.CONTROLLER:
            stream_logs_code = (
                serve_utils.ServeCodeGen.stream_serve_process_logs(
                    service_name,
                    stream_controller=True,
                    follow=False,
                    tail=tail,
                    pool=pool))
        elif component == serve_utils.ServiceComponent.LOAD_BALANCER:
            stream_logs_code = (
                serve_utils.ServeCodeGen.stream_serve_process_logs(
                    service_name,
                    stream_controller=False,
                    follow=False,
                    tail=tail,
                    pool=pool))
        elif component == serve_utils.ServiceComponent.REPLICA:
            replica_id = target.replica_id
            assert replica_id is not None, service_name
            stream_logs_code = serve_utils.ServeCodeGen.stream_replica_logs(
                service_name, replica_id, follow=False, tail=tail, pool=pool)
        else:
            assert False, component

        # Refer to the notes in
        # sky/backends/cloud_vm_ray_backend.py::CloudVmRayBackend::tail_logs.
        backend.run_on_head(handle,
                            stream_logs_code,
                            stream_logs=False,
                            process_stream=False,
                            ssh_mode=command_runner.SshMode.INTERACTIVE,
                            log_path=log_path)

    subprocess_utils.run_in_parallel(sync_down_logs_by_target,
                                     list(normalized_targets))

    return local_dir
