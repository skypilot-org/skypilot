"""Sky backend interface."""
import typing
from typing import Dict, Optional

if typing.TYPE_CHECKING:
    from sky import resources
    from sky import task as task_lib

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

    def provision(self,
                  task: 'task_lib.Task',
                  to_provision: Optional['resources.Resources'],
                  dryrun: bool,
                  stream_logs: bool,
                  cluster_name: Optional[str] = None) -> ResourceHandle:
        raise NotImplementedError

    def sync_workdir(self, handle: ResourceHandle, workdir: Path) -> None:
        raise NotImplementedError

    def sync_file_mounts(
        self,
        handle: ResourceHandle,
        all_file_mounts: Dict[Path, Path],
        cloud_to_remote_file_mounts: Optional[Dict[Path, Path]],
    ) -> None:
        raise NotImplementedError

    def setup(self, handle: ResourceHandle, task: 'task_lib.Task') -> None:
        raise NotImplementedError

    def add_storage_objects(self, task: 'task_lib.Task') -> None:
        raise NotImplementedError

    def execute(self, handle: ResourceHandle, task: 'task_lib.Task',
                detach_run: bool) -> None:
        raise NotImplementedError

    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        """Post execute(): e.g., print helpful inspection messages."""
        raise NotImplementedError

    def teardown_ephemeral_storage(self, task: 'task_lib.Task') -> None:
        raise NotImplementedError

    def teardown(self, handle: ResourceHandle, terminate: bool) -> bool:
        raise NotImplementedError

    def register_info(self, **kwargs) -> None:
        """Register backend-specific information."""
        pass
