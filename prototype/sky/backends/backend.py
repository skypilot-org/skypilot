"""Sky backend interface."""
from typing import Any, Callable, Dict, Optional

from sky import resources
from sky import task as task_lib

Task = task_lib.Task
Resources = resources.Resources
Path = str
PostSetupFn = Callable[[str], Any]


class Backend:
    """Backend interface: handles provisioning, setup, and scheduling."""

    # Backend-specific handle to the launched resources (e.g., a cluster).
    # Examples: 'cluster.yaml'; 'ray://...', 'k8s://...'.
    class ResourceHandle:
        pass

    def provision(self,
                  task: Task,
                  to_provision: Resources,
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

    def add_storage_objects(self, task: Task) -> None:
        raise NotImplementedError

    def run_post_setup(self, handle: ResourceHandle, post_setup_fn: PostSetupFn,
                       task: Task) -> None:
        raise NotImplementedError

    def execute(self, handle: ResourceHandle, task: Task,
                detach_run: bool) -> None:
        raise NotImplementedError

    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        """Post execute(): e.g., print helpful inspection messages."""
        raise NotImplementedError

    def teardown_ephemeral_storage(self, task: Task) -> None:
        raise NotImplementedError

    def teardown(self, handle: ResourceHandle, terminate: bool) -> None:
        raise NotImplementedError

    def register_info(self, **kwargs) -> None:
        """Register backend-specific information."""
        pass
