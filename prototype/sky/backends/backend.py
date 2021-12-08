"""Sky backend interface."""
from typing import Any, Callable, Dict, Optional

from sky import resources
from sky.backends import backend_utils

App = backend_utils.App
Resources = resources.Resources
Path = str
PostSetupFn = Callable[[str], Any]


class Backend(object):
    """Backend interface: handles provisioning, setup, and scheduling."""

    # Backend-specific handle to the launched resources (e.g., a cluster).
    # Examples: 'cluster.yaml'; 'ray://...', 'k8s://...'.
    class ResourceHandle(object):
        pass

    def provision(self,
                  task: App,
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

    def add_storage_backend(self, task: App, cloud_type: str) -> None:
        raise NotImplementedError

    def run_post_setup(self, handle: ResourceHandle, post_setup_fn: PostSetupFn,
                       task: App) -> None:
        raise NotImplementedError

    def execute(self, handle: ResourceHandle, task: App,
                stream_logs: bool) -> None:
        raise NotImplementedError

    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        """Post execute(): e.g., print helpful inspection messages."""
        raise NotImplementedError

    def teardown(self, handle: ResourceHandle) -> None:
        raise NotImplementedError

    def register_info(self, **kwargs) -> None:
        """Register backend-specific information."""
        pass
