"""Sky backend interface."""
import typing
from typing import Dict, Generic, Optional

import sky
from sky.usage import usage_lib
from sky.utils import timeline

if typing.TYPE_CHECKING:
    from sky import resources
    from sky import task as task_lib
    from sky.data import storage as storage_lib

Path = str
_ResourceHandleType = typing.TypeVar('_ResourceHandleType',
                                     bound='ResourceHandle')


# Backend-specific handle to the launched resources (e.g., a cluster).
# Examples: 'cluster.yaml'; 'ray://...', 'k8s://...'.
class ResourceHandle:

    def get_cluster_name(self) -> str:
        raise NotImplementedError


class Backend(Generic[_ResourceHandleType]):
    """Backend interface: handles provisioning, setup, and scheduling."""

    # NAME is used to identify the backend class from cli/yaml.
    NAME = 'backend'

    # Backward compatibility, with the old name of the handle.
    ResourceHandle = ResourceHandle  # pylint: disable=invalid-name

    # --- APIs ---
    def check_resources_fit_cluster(self, handle: _ResourceHandleType,
                                    task: 'task_lib.Task') -> None:
        """Check whether resources of the task are satisfied by cluster."""
        raise NotImplementedError

    @timeline.event
    @usage_lib.messages.usage.update_runtime('provision')
    def provision(
            self,
            task: 'task_lib.Task',
            to_provision: Optional['resources.Resources'],
            dryrun: bool,
            stream_logs: bool,
            cluster_name: Optional[str] = None,
            retry_until_up: bool = False) -> Optional[_ResourceHandleType]:
        if cluster_name is None:
            cluster_name = sky.backends.backend_utils.generate_cluster_name()
        usage_lib.record_cluster_name_for_current_operation(cluster_name)
        usage_lib.messages.usage.update_actual_task(task)
        return self._provision(task, to_provision, dryrun, stream_logs,
                               cluster_name, retry_until_up)

    @timeline.event
    @usage_lib.messages.usage.update_runtime('sync_workdir')
    def sync_workdir(self, handle: _ResourceHandleType, workdir: Path) -> None:
        return self._sync_workdir(handle, workdir)

    @timeline.event
    @usage_lib.messages.usage.update_runtime('sync_file_mounts')
    def sync_file_mounts(
        self,
        handle: _ResourceHandleType,
        all_file_mounts: Optional[Dict[Path, Path]],
        storage_mounts: Optional[Dict[Path, 'storage_lib.Storage']],
    ) -> None:
        return self._sync_file_mounts(handle, all_file_mounts, storage_mounts)

    @timeline.event
    @usage_lib.messages.usage.update_runtime('setup')
    def setup(self, handle: _ResourceHandleType, task: 'task_lib.Task',
              detach_setup: bool) -> None:
        return self._setup(handle, task, detach_setup)

    def add_storage_objects(self, task: 'task_lib.Task') -> None:
        raise NotImplementedError

    @timeline.event
    @usage_lib.messages.usage.update_runtime('execute')
    def execute(self,
                handle: _ResourceHandleType,
                task: 'task_lib.Task',
                detach_run: bool,
                dryrun: bool = False) -> Optional[int]:
        """Execute the task on the cluster.

        Returns:
            Job id if the task is submitted to the cluster, None otherwise.
        """
        usage_lib.record_cluster_name_for_current_operation(
            handle.get_cluster_name())
        usage_lib.messages.usage.update_actual_task(task)
        return self._execute(handle, task, detach_run, dryrun)

    @timeline.event
    def post_execute(self, handle: _ResourceHandleType, down: bool) -> None:
        """Post execute(): e.g., print helpful inspection messages."""
        return self._post_execute(handle, down)

    @timeline.event
    def teardown_ephemeral_storage(self, task: 'task_lib.Task') -> None:
        return self._teardown_ephemeral_storage(task)

    @timeline.event
    @usage_lib.messages.usage.update_runtime('teardown')
    def teardown(self,
                 handle: _ResourceHandleType,
                 terminate: bool,
                 purge: bool = False) -> None:
        self._teardown(handle, terminate, purge)

    def register_info(self, **kwargs) -> None:
        """Register backend-specific information."""
        pass

    # --- Implementations of the APIs ---
    def _provision(
            self,
            task: 'task_lib.Task',
            to_provision: Optional['resources.Resources'],
            dryrun: bool,
            stream_logs: bool,
            cluster_name: str,
            retry_until_up: bool = False) -> Optional[_ResourceHandleType]:
        raise NotImplementedError

    def _sync_workdir(self, handle: _ResourceHandleType, workdir: Path) -> None:
        raise NotImplementedError

    def _sync_file_mounts(
        self,
        handle: _ResourceHandleType,
        all_file_mounts: Optional[Dict[Path, Path]],
        storage_mounts: Optional[Dict[Path, 'storage_lib.Storage']],
    ) -> None:
        raise NotImplementedError

    def _setup(self, handle: _ResourceHandleType, task: 'task_lib.Task',
               detach_setup: bool) -> None:
        raise NotImplementedError

    def _execute(self,
                 handle: _ResourceHandleType,
                 task: 'task_lib.Task',
                 detach_run: bool,
                 dryrun: bool = False) -> Optional[int]:
        raise NotImplementedError

    def _post_execute(self, handle: _ResourceHandleType, down: bool) -> None:
        raise NotImplementedError

    def _teardown_ephemeral_storage(self, task: 'task_lib.Task') -> None:
        raise NotImplementedError

    def _teardown(self,
                  handle: _ResourceHandleType,
                  terminate: bool,
                  purge: bool = False):
        raise NotImplementedError
