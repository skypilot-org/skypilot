"""Local docker backend for sky"""
from typing import Any, Callable, Dict, Optional

import colorama
import docker

from sky import backends
from sky import logging
from sky import resources
from sky import task as task_mod
from sky.backends import backend_utils
from sky.backends import docker_utils

App = backend_utils.App
Resources = resources.Resources
Path = str
PostSetupFn = Callable[[str], Any]

logger = logging.init_logger(__name__)


class LocalDockerBackend(backends.Backend):
    """Local docker backend for debugging. Ignores resource demands when allocatinng."""
    # Resource handle is simply the name of the task
    ResourceHandle = Any

    def __init__(self):
        self.volume_mounts = {}  # Stores the ResourceHandle->volume mounts map
        self.images = {}  # Stores the ResourceHandle->images map
        self.containers = {}
        self.client = docker.from_env()

    def provision(self, task: App, to_provision: Resources,
                  dryrun: bool) -> ResourceHandle:
        """ Since resource demands are ignored, There's no provisioning in
         local docker. Simply return the task name as the handle."""
        handle = task.name
        logger.info(
            f'Building docker image for task {task.name}. This might take some time.'
        )
        image_tag = docker_utils.build_dockerimage_from_task(task)
        self.images[handle] = image_tag
        logger.info(f'Image {image_tag} built.')
        logger.info('Provisioning complete.')
        return task.name

    def sync_workdir(self, handle: ResourceHandle, workdir: Path) -> None:
        """ Workdir is sync'd by adding to the docker image.
        This happens in the execute step."""
        logger.info('Since the workdir is synced at build time, '
                    'sync_workdir is a NoOp.')

    def sync_file_mounts(self, handle: ResourceHandle,
                         all_file_mounts: Dict[Path, Path],
                         cloud_to_remote_file_mounts: Optional[Dict[Path, Path]]
                        ) -> None:
        """ File mounts in Docker are implemented with volume mounts using the -v flag"""
        assert cloud_to_remote_file_mounts is None, 'Only local file mounts are supported' \
                                                    ' with LocalDockerBackend'
        docker_mounts = {}
        if all_file_mounts:
            for container_path, local_path in all_file_mounts.items():
                docker_mounts[local_path] = {
                    'bind': container_path,
                    'mode': 'rw'
                }
        self.volume_mounts[handle] = docker_mounts

    def run_post_setup(self, handle: ResourceHandle, post_setup_fn: PostSetupFn,
                       task: App) -> None:
        """Post setup is tricky to do in LocalDockerBackend.
        Future investigation: Can we support it with a custom ENTRYPOINT?."""
        logger.warning(
            'Post setup is currently not supported in LocalDockerBackend')

    def execute(self, handle: ResourceHandle, task: App,
                stream_logs: bool) -> None:
        """ Launches the container."""

        # ParTask and Tasks with more than 1 nodes are not currently supported
        if isinstance(task, task_mod.ParTask):
            raise NotImplementedError(
                'ParTask is currently not supported in LocalDockerBackend.')

        if task.num_nodes > 1:
            raise NotImplementedError(
                'Tasks with num_nodes > 1 is currently not supported in LocalDockerBackend.'
            )

        # Handle a basic task
        if task.run is None:
            logger.info(f'Nothing to run; run command not specified:\n{task}')
            return

        self._execute_task_one_node(handle, task)

    def _execute_task_one_node(self, handle: ResourceHandle,
                               task: task_mod.Task) -> None:
        assert handle in self.images[
            handle], f'No image found for {handle}, have you run Backend.provision()?'
        image_tag = self.images[handle]
        logger.info(f'Image {image_tag} found. Running container now.')
        container = self.client.containers.run(image_tag,
                                               remove=True,
                                               detach=True)
        self.containers[handle] = container
        logger.info(
            f'Your container is now running with name {container.name}. You can debug by running docker run -it {image_tag} /bin/bash.'
        )
        logger.info(f'*** Container output {container.name} ***')
        for line in container.logs(stream=True):
            logger.info(line.strip())
        logger.info(f'*** Container {container.name} has terminated ***.')

    def post_execute(self, handle: ResourceHandle, teardown: bool) -> None:
        colorama.init()
        Style = colorama.Style
        container = self.containers[handle]
        logger.info(
            f'Your container is now running with name {Style.BRIGHT}{container.name}{Style.RESET_ALL}'
        )
        logger.info(
            f'To get a shell in your container, run {Style.BRIGHT}docker exec -it {container.image.tags[0]} /bin/bash{Style.RESET_ALL}'
        )
        logger.info(
            f'To create a new container for debugging without running the task run command,'
            f' run {Style.BRIGHT}docker run -it {container.image.tags[0]} /bin/bash{Style.RESET_ALL}'
        )

    def teardown(self, handle: ResourceHandle) -> None:
        """ Teardown kills the container"""
        self.containers[handle].remove(force=True)
