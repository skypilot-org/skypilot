"""Local docker backend for sky"""
from typing import Any, Callable, Dict, Optional, Union

import colorama
import docker

from sky import backends
from sky import logging
from sky import resources
from sky import task as task_lib
from sky.backends import backend_utils
from sky.backends import docker_utils

Task = task_lib.Task
Resources = resources.Resources
Path = str
PostSetupFn = Callable[[str], Any]

logger = logging.init_logger(__name__)


class LocalDockerBackend(backends.Backend):
    """Local docker backend for debugging.

    Ignores resource demands when allocating. Optionally uses GPU if required.
    """

    class ResourceHandle(str):
        """The name of the task."""
        pass

    # Define the Docker-in-Docker mount
    _dind_mount = {
        '/var/run/docker.sock': {
            'bind': '/var/run/docker.sock',
            'mode': 'rw'
        }
    }

    def __init__(self, use_gpu: Union[bool, str] = 'auto'):
        """
        Args:
            use_gpu: Whether to use GPUs. Either of True, False or 'auto'.
              Sets container runtime to 'nvidia' if set to True, else uses the
              default runtime. If set to 'auto', it detects if GPUs are present
              and automatically sets the container runtime.

        """
        self._use_gpu = backend_utils.check_local_gpus() if use_gpu == 'auto' \
            else use_gpu
        self.volume_mounts = {}  # Stores the ResourceHandle->volume mounts map
        self.images = {}  # Stores the ResourceHandle->images map
        self.containers = {}
        self.client = docker.from_env()

    def provision(self,
                  task: Task,
                  to_provision: Resources,
                  dryrun: bool,
                  stream_logs: bool,
                  cluster_name: Optional[str] = None) -> ResourceHandle:
        """Simply returns the task name as the handle.

        Since resource demands are ignored, There's no provisioning in local
        docker.
        """
        del cluster_name  # Unused.
        if stream_logs:
            logger.info(
                'Streaming build logs is not supported in LocalDockerBackend. '
                'Build logs will be shown on failure.')
        handle = task.name
        assert handle is not None, 'Task should have a name to be run with ' \
                                   'LocalDockerBackend.'
        logger.info(f'Building docker image for task {task.name}. '
                    'This might take some time.')
        image_tag = docker_utils.build_dockerimage_from_task(task)
        self.images[handle] = image_tag
        logger.info(f'Image {image_tag} built.')
        logger.info('Provisioning complete.')
        return task.name

    def sync_workdir(self, handle: ResourceHandle, workdir: Path) -> None:
        """Workdir is sync'd by adding to the docker image.

        This happens in the execute step.
        """
        logger.info('Since the workdir is synced at build time, '
                    'sync_workdir is a NoOp.')

    def sync_file_mounts(
            self,
            handle: ResourceHandle,
            all_file_mounts: Dict[Path, Path],
            cloud_to_remote_file_mounts: Optional[Dict[Path, Path]],
    ) -> None:
        """File mounts in Docker are implemented with volume mounts (-v)."""
        assert not cloud_to_remote_file_mounts, \
            'Only local file mounts are supported with LocalDockerBackend.'
        docker_mounts = {}

        # Add DIND socket mount
        docker_mounts.update(LocalDockerBackend._dind_mount)

        # Add other mounts
        if all_file_mounts:
            for container_path, local_path in all_file_mounts.items():
                docker_mounts[local_path] = {
                    'bind': container_path,
                    'mode': 'rw'
                }
        self.volume_mounts[handle] = docker_mounts

    def run_post_setup(self, handle: ResourceHandle, post_setup_fn: PostSetupFn,
                       task: Task) -> None:
        """Post setup is tricky to do in LocalDockerBackend.

        Future investigation: Can we support it with a custom ENTRYPOINT?.
        """
        logger.warning(
            'Post setup is currently not supported in LocalDockerBackend')

    def execute(self, handle: ResourceHandle, task: Task, stream_logs: bool,
                detach_run: bool) -> None:
        """ Launches the container."""

        # FIXME: handle the detach_run
        if not detach_run:
            raise NotImplementedError('detach_run=False is not supported in '
                                      'LocalDockerBackend.')

        if task.num_nodes > 1:
            raise NotImplementedError(
                'Tasks with num_nodes > 1 is currently not supported in '
                'LocalDockerBackend.')

        # Handle a basic task
        if task.run is None:
            logger.info(f'Nothing to run; run command not specified:\n{task}')
            return

        self._execute_task_one_node(handle, task)

    def _execute_task_one_node(self, handle: ResourceHandle,
                               task: Task) -> None:
        assert task.name == handle, (task.name, handle)
        colorama.init()
        style = colorama.Style
        assert handle in self.images[handle], \
            f'No image found for {handle}, have you run Backend.provision()?'
        image_tag = self.images[handle]
        volumes = self.volume_mounts[handle]
        runtime = 'nvidia' if self._use_gpu else None
        logger.info(f'Image {image_tag} found. Running container now. use_gpu '
                    f'is {self._use_gpu}')
        try:
            container = self.client.containers.run(image_tag,
                                                   remove=True,
                                                   detach=True,
                                                   privileged=True,
                                                   volumes=volumes,
                                                   runtime=runtime)
        except docker.errors.APIError as e:
            if 'Unknown runtime specified nvidia' in e.explanation:
                logger.error(
                    'Unable to run container - nvidia runtime for docker not '
                    'found. Have you installed nvidia-docker on your machine?')
            raise e
        self.containers[handle] = container
        logger.info(
            f'Your container is now running with name {container.name}.\n'
            f'To get a shell in your container, run {style.BRIGHT}docker exec '
            f'-it {container.name} /bin/bash{style.RESET_ALL}.\n'
            f'You can debug the image by running {style.BRIGHT}docker run -it '
            f'{image_tag} /bin/bash{style.RESET_ALL}.\n')
        logger.info(f'*** Container output {container.name} ***')
        for line in container.logs(stream=True):
            logger.info(line.strip())
        logger.info(f'*** Container {container.name} has terminated ***.')

    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        colorama.init()
        style = colorama.Style
        container = self.containers[handle]

        # Fetch latest status from docker daemon
        container.reload()

        if container.status == 'running':
            logger.info('Your container is now running with name '
                        f'{style.BRIGHT}{container.name}{style.RESET_ALL}')
            logger.info(
                f'To get a shell in your container, run {style.BRIGHT}docker '
                f'exec -it {container.name} /bin/bash{style.RESET_ALL}')
        else:
            logger.info('Your container has finished running. Name was '
                        f'{style.BRIGHT}{container.name}{style.RESET_ALL}')
        logger.info(
            'To create a new container for debugging without running the '
            f'task run command, run {style.BRIGHT}docker run -it '
            f'{container.image.tags[0]} /bin/bash{style.RESET_ALL}')

    def teardown(self, handle: ResourceHandle, terminate: bool) -> None:
        """Teardown kills the container."""
        if not terminate:
            logger.warning(
                'LocalDockerBackend.teardown() will terminate '
                'containers for now, despite receiving termiante=False.')
        self.containers[handle].remove(force=True)
