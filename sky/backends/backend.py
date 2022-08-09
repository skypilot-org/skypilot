"""Sky backend interface."""
import typing
from typing import Dict, Optional

import sky
from sky.utils import timeline
from sky.usage import usage_lib

if typing.TYPE_CHECKING:
    from sky import resources
    from sky import task as task_lib
    from sky.data import storage as storage_lib

Path = str


class Backend:
    """Backend interface: handles provisioning, setup, and scheduling."""

    # NAME is used to identify the backend class from cli/yaml.
    NAME = 'backend'

    # Backend-specific handle to the launched resources (e.g., a cluster).
    # Examples: 'cluster.yaml'; 'ray://...', 'k8s://...'.
    class ResourceHandle:

        def get_cluster_name(self) -> str:
            raise NotImplementedError

    # --- APIs ---
    def check_resources_fit_cluster(self, handle: ResourceHandle,
                                    task: 'task_lib.Task') -> None:
        """Check whether resources of the task are satisfied by cluster."""
        raise NotImplementedError

    @timeline.event
    @usage_lib.messages.usage.update_runtime('provision')
    def provision(self,
                  task: 'task_lib.Task',
                  to_provision: Optional['resources.Resources'],
                  dryrun: bool,
                  stream_logs: bool,
                  cluster_name: Optional[str] = None,
                  retry_until_up: bool = False) -> ResourceHandle:
        if cluster_name is None:
            cluster_name = sky.backends.backend_utils.generate_cluster_name()
        usage_lib.messages.usage.update_cluster_name(cluster_name)
        usage_lib.messages.usage.update_actual_task(task)
        return self._provision(task, to_provision, dryrun, stream_logs,
                               cluster_name, retry_until_up)

    @timeline.event
    @usage_lib.messages.usage.update_runtime('sync_workdir')
    def sync_workdir(self, handle: ResourceHandle, workdir: Path) -> None:
        return self._sync_workdir(handle, workdir)

    @timeline.event
    @usage_lib.messages.usage.update_runtime('sync_file_mounts')
    def sync_file_mounts(
        self,
        handle: ResourceHandle,
        all_file_mounts: Dict[Path, Path],
        storage_mounts: Dict[Path, 'storage_lib.Storage'],
    ) -> None:
        return self._sync_file_mounts(handle, all_file_mounts, storage_mounts)

    @timeline.event
    @usage_lib.messages.usage.update_runtime('setup')
    def setup(self, handle: ResourceHandle, task: 'task_lib.Task') -> None:
        return self._setup(handle, task)

    def add_storage_objects(self, task: 'task_lib.Task') -> None:
        raise NotImplementedError

    @timeline.event
    @usage_lib.messages.usage.update_runtime('execute')
    def execute(self, handle: ResourceHandle, task: 'task_lib.Task',
                detach_run: bool) -> None:
        usage_lib.messages.usage.update_cluster_name(handle.get_cluster_name())
        usage_lib.messages.usage.update_actual_task(task)
        return self._execute(handle, task, detach_run)

    @timeline.event
    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        """Post execute(): e.g., print helpful inspection messages."""
        return self._post_execute(handle, teardown)

    @timeline.event
    def teardown_ephemeral_storage(self, task: 'task_lib.Task') -> None:
        return self._teardown_ephemeral_storage(task)

    @timeline.event
    @usage_lib.messages.usage.update_runtime('teardown')
    def teardown(self,
                 handle: ResourceHandle,
                 terminate: bool,
                 purge: bool = False) -> None:
        self._teardown(handle, terminate, purge)

    def register_info(self, **kwargs) -> None:
        """Register backend-specific information."""
        pass

    # --- Implementations of the APIs ---
    def _provision(self,
                   task: 'task_lib.Task',
                   to_provision: Optional['resources.Resources'],
                   dryrun: bool,
                   stream_logs: bool,
                   cluster_name: str,
                   retry_until_up: bool = False) -> ResourceHandle:
        raise NotImplementedError

    def _sync_workdir(self, handle: ResourceHandle, workdir: Path) -> None:
        raise NotImplementedError

    def _sync_file_mounts(
        self,
        handle: ResourceHandle,
        all_file_mounts: Dict[Path, Path],
        storage_mounts: Dict[Path, 'storage_lib.Storage'],
    ) -> None:
        raise NotImplementedError

    def _setup(self, handle: ResourceHandle, task: 'task_lib.Task') -> None:
        raise NotImplementedError

    def _execute(self, handle: ResourceHandle, task: 'task_lib.Task',
                 detach_run: bool) -> None:
        raise NotImplementedError

    def _post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        raise NotImplementedError

    def _teardown_ephemeral_storage(self, task: 'task_lib.Task') -> None:
        raise NotImplementedError

    def _teardown(self,
                  handle: ResourceHandle,
                  terminate: bool,
                  purge: bool = False):
        raise NotImplementedError
