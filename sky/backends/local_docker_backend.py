"""Local docker backend for sky"""
import subprocess
import tempfile
import typing
from typing import Any, Dict, Optional, Tuple, Union

import colorama

from sky import backends
from sky import global_user_state
from sky import sky_logging
from sky.adaptors import docker
from sky.backends import backend_utils
from sky.backends import docker_utils
from sky.data import storage as storage_lib
from sky.utils import rich_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources
    from sky import task as task_lib

Path = str

logger = sky_logging.init_logger(__name__)

_DOCKER_RUN_FOREVER_CMD = 'tail -f /dev/null'
_DOCKER_DEFAULT_LABELS = {'app': 'sky'}
_DOCKER_LABEL_PREFIX = 'skymeta_'
_DOCKER_HANDLE_PREFIX = 'skydocker-'


class LocalDockerResourceHandle(str, backends.ResourceHandle):
    """The name of the cluster/container prefixed with the handle prefix."""

    def __new__(cls, s, **kw):
        if s.startswith(_DOCKER_HANDLE_PREFIX):
            prefixed_str = s
        else:
            prefixed_str = _DOCKER_HANDLE_PREFIX + s
        return str.__new__(cls, prefixed_str, **kw)

    def get_cluster_name(self):
        return self.lstrip(_DOCKER_HANDLE_PREFIX)


class LocalDockerBackend(backends.Backend['LocalDockerResourceHandle']):
    """Local docker backend for debugging.

    Ignores resource demands when allocating. Optionally uses GPU if required.

    Here's a correspondence map to help understand how sky concepts map to
    Docker concepts:

    * Sky cluster is a Docker container.

    * Each task is mapped to a docker image. Docker caches individual layers
      of images behind the scenes, so image is built only the first time a task
      is run (unless the setup or workdir changes later).

    * Provisioning involves building a docker image locally and creating a
      container with a sleep task (tail -f /dev/null). (Task run commands are
      not used as docker run commands because in docker, once the task exits,
      the container also terminates. The sleep task in our implementation keeps
      the container alive and allows us to use docker exec to run tasks.)

    * Sky tasks are executed in the container (cluster) through docker exec.
      I.e., sky.execute calls docker.container.exec

    * Sky stop is equivalent to sky down - both terminate the container.

    Some Notes:

    * GPU acceleration is available on containers where supported.

    * Since there's no easy way to track the output of a docker exec command in
      detached mode, this backend does not support the `-d` flag (and
      consequently, job queue features are also not supported).

    * Ctrl+C on a running command kills the process inside the container.
      (from the API, it's possible to let it run in the background. However,
      getting logs from the API is tricky)

    * This backend requires task.name to be set. This is a hard requirement
      because task.name is used as the image tag. Randomly generated task names
      aren't a clean solution because the user's local docker tag registry would
      get littered with multiple names (each time a task is run).

    * There's no notion of resources - this can be constrained if required,
      but not implemented currently.
    """

    NAME = 'localdocker'

    # Backward compatibility, with the old name of the handle.
    ResourceHandle = LocalDockerResourceHandle  # pylint: disable=invalid-name

    # Define the Docker-in-Docker mount
    _dind_mount = {
        '/var/run/docker.sock': {
            'bind': '/var/run/docker.sock',
            'mode': 'rw'
        }
    }

    def __init__(self, use_gpu: Union[bool, str] = 'auto'):
        """Local docker backend.

        Args:
            use_gpu: Whether to use GPUs. Either of True, False or 'auto'.
              Sets container runtime to 'nvidia' if set to True, else uses the
              default runtime. If set to 'auto', it detects if GPUs are present
              and automatically sets the container runtime.

        """
        self._use_gpu = backend_utils.check_local_gpus() if use_gpu == 'auto' \
            else use_gpu
        self.volume_mounts: Dict[LocalDockerResourceHandle, Dict[str, Any]] = {
        }  # Stores the LocalDockerResourceHandle->volume mounts map
        self.images: Dict[LocalDockerResourceHandle, Tuple[str, Dict[
            str, str]]] = {
            }  # Stores the LocalDockerResourceHandle->[image_tag, metadata] map
        self.containers: Dict[LocalDockerResourceHandle, Any] = {}
        self.client = docker.from_env()
        self._update_state()

    # --- Implementation of Backend APIs ---

    def check_resources_fit_cluster(self, handle: 'LocalDockerResourceHandle',
                                    task: 'task_lib.Task') -> None:
        pass

    def _provision(
        self,
        task: 'task_lib.Task',
        to_provision: Optional['resources.Resources'],
        dryrun: bool,
        stream_logs: bool,
        cluster_name: str,
        retry_until_up: bool = False,
        skip_unnecessary_provisioning: bool = False,
    ) -> Optional[LocalDockerResourceHandle]:
        """Builds docker image for the task and returns cluster name as handle.

        Since resource demands are ignored, There's no provisioning in local
        docker.
        """
        del to_provision  # Unused
        assert task.name is not None, ('Task name cannot be None - have you '
                                       'specified a task name?')
        if dryrun:
            return None
        if retry_until_up:
            logger.warning(
                f'Retrying until up is not supported in backend: {self.NAME}. '
                'Ignored the flag.')
        if skip_unnecessary_provisioning:
            logger.warning(f'skip_unnecessary_provisioning is not supported in '
                           f'backend: {self.NAME}. Ignored the flag.')
        if stream_logs:
            logger.info(
                'Streaming build logs is not supported in LocalDockerBackend. '
                'Build logs will be shown on failure.')
        handle = LocalDockerResourceHandle(cluster_name)
        logger.info(f'Building docker image for task {task.name}. '
                    'This might take some time.')
        with rich_utils.safe_status(
                ux_utils.spinner_message('Building Docker image')):
            image_tag, metadata = docker_utils.build_dockerimage_from_task(task)
        self.images[handle] = (image_tag, metadata)
        logger.info(f'Image {image_tag} built.')
        logger.info('Provisioning complete.')
        global_user_state.add_or_update_cluster(cluster_name,
                                                cluster_handle=handle,
                                                requested_resources=set(
                                                    task.resources),
                                                ready=False)
        return handle

    def _sync_workdir(self, handle: LocalDockerResourceHandle,
                      workdir: Path) -> None:
        """Workdir is sync'd by adding to the docker image.

        This happens in the execute step.
        """
        del handle, workdir  # Unused
        logger.info('Since the workdir is synced at build time, sync_workdir is'
                    ' a NoOp. If you are running sky exec, your workdir has not'
                    ' been updated.')

    def _sync_file_mounts(
        self,
        handle: LocalDockerResourceHandle,
        all_file_mounts: Optional[Dict[Path, Path]],
        storage_mounts: Optional[Dict[Path, storage_lib.Storage]],
    ) -> None:
        """File mounts in Docker are implemented with volume mounts (-v)."""
        assert not storage_mounts, \
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

    def _setup(self, handle: LocalDockerResourceHandle, task: 'task_lib.Task',
               detach_setup: bool) -> None:
        """Launches a container and runs a sleep command on it.

        setup() in LocalDockerBackend runs the container with a sleep job
        so that the container is kept alive and we can issue docker exec cmds
        to it to handle sky exec commands.
        """
        del detach_setup  # unused
        style = colorama.Style
        assert handle in self.images, \
            f'No image found for {handle}, have you run Backend.provision()?'
        image_tag, metadata = self.images[handle]
        volumes = self.volume_mounts[handle]
        runtime = 'nvidia' if self._use_gpu else None
        logger.info(f'Image {image_tag} found. Running container now. use_gpu '
                    f'is {self._use_gpu}')
        cluster_name = handle.get_cluster_name()
        # Encode metadata in docker labels:
        labels = {f'{_DOCKER_LABEL_PREFIX}{k}': v for k, v in metadata.items()}
        labels.update(_DOCKER_DEFAULT_LABELS)
        try:
            # Check if a container exists and remove it to create new one
            _ = self.client.containers.get(handle)  # Throws NotFound error
            self.teardown(handle, terminate=True)
        except docker.not_found_error():
            # Container does not exist, we're good to go
            pass
        try:
            container = self.client.containers.run(
                image_tag,
                name=handle,
                command=_DOCKER_RUN_FOREVER_CMD,
                remove=True,
                detach=True,
                privileged=True,
                volumes=volumes,
                runtime=runtime,
                labels=labels)
        except docker.api_error() as e:
            if 'Unknown runtime specified nvidia' in e.explanation:
                logger.error(
                    'Unable to run container - nvidia runtime for docker not '
                    'found. Have you installed nvidia-docker on your machine?')
            global_user_state.remove_cluster(cluster_name, terminate=True)
            raise e
        self.containers[handle] = container
        logger.info(
            f'Your container is now running with name: {container.name}.\n'
            f'To get a shell in your container, run: {style.BRIGHT}docker exec '
            f'-it {container.name} /bin/bash{style.RESET_ALL}.\n'
            f'You can debug the image by running: {style.BRIGHT}docker run -it '
            f'{image_tag} /bin/bash{style.RESET_ALL}.\n')
        global_user_state.add_or_update_cluster(cluster_name,
                                                cluster_handle=handle,
                                                requested_resources=set(
                                                    task.resources),
                                                ready=True)

    def _execute(self,
                 handle: LocalDockerResourceHandle,
                 task: 'task_lib.Task',
                 detach_run: bool,
                 dryrun: bool = False) -> None:
        """ Launches the container."""

        if detach_run:
            raise NotImplementedError('detach_run=True is not supported in '
                                      'LocalDockerBackend.')

        if task.num_nodes > 1:
            raise NotImplementedError(
                'Tasks with num_nodes > 1 is currently not supported in '
                'LocalDockerBackend.')

        # Handle a basic task
        if task.run is None:
            logger.info(f'Nothing to run; run command not specified:\n{task}')
            return

        if dryrun:
            logger.info(f'Dryrun complete. Would have run:\n{task}')
            return

        self._execute_task_one_node(handle, task)

    def _post_execute(self, handle: LocalDockerResourceHandle,
                      down: bool) -> None:
        del down  # unused
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

    def _teardown(self,
                  handle: LocalDockerResourceHandle,
                  terminate: bool,
                  purge: bool = False):
        """Teardown kills the container."""
        del purge  # Unused.
        if not terminate:
            logger.warning(
                'LocalDockerBackend.teardown() will terminate '
                'containers for now, despite receiving terminate=False.')

        # If handle is not found in the self.containers, it implies it has
        # already been removed externally in docker. No action is needed
        # except for removing it from global_user_state.
        if handle in self.containers:
            container = self.containers[handle]
            container.remove(force=True)
        cluster_name = handle.get_cluster_name()

        global_user_state.remove_cluster(cluster_name, terminate=True)

    # --- Utilities ---

    def _update_state(self):
        """Updates local state of the backend object.

        Queries the docker daemon to get the list of running containers, and
        populates the self.images and self.containers attributes from metadata
        of running containers.

        Metadata is stored with running containers in the form of container
        labels prefixed by skymeta_ (e.g. skymeta_workdir).
        """
        search_filter = {
            'label': [f'{k}={v}' for k, v in _DOCKER_DEFAULT_LABELS.items()]
        }
        containers = self.client.containers.list(filters=search_filter)
        for c in containers:
            # Extract container/image metadata
            metadata = {}
            for k, v in c.labels.items():
                if k.startswith(_DOCKER_LABEL_PREFIX):
                    # Remove 'skymeta_' from key
                    metadata[k[len(_DOCKER_LABEL_PREFIX):]] = v
            self.images[c.name] = [c.image, metadata]
            self.containers[c.name] = c

    def _execute_task_one_node(self, handle: LocalDockerResourceHandle,
                               task: 'task_lib.Task') -> None:
        if callable(task.run):
            raise NotImplementedError(
                'Tasks with callable run commands are not supported in '
                'LocalDockerBackend.')
        container = self.containers[handle]
        _, image_metadata = self.images[handle]
        with tempfile.NamedTemporaryFile(mode='w') as temp_file:
            script_contents = docker_utils.bash_codegen(
                workdir_name=image_metadata['workdir_name'],
                multiline_cmds=task.run)
            temp_file.write(script_contents)
            temp_file.flush()
            script_path = temp_file.name
            cmd = f'chmod +x {script_path} && docker cp {script_path} ' \
                  f'{container.name}:/sky/{docker_utils.SKY_DOCKER_RUN_SCRIPT}'
            subprocess.run(cmd, shell=True, check=True)

        _, exec_log = container.exec_run(
            cmd=f'/bin/bash -c "./sky/{docker_utils.SKY_DOCKER_RUN_SCRIPT}"',
            stdout=True,
            stderr=True,
            stdin=True,
            tty=True,
            stream=True,
            privileged=True)

        # For consistency in behavior with CloudVMRayBackend, we catch ctrl+c
        # during execution and manually kill the process in the container
        try:
            for line in exec_log:
                logger.info(line.decode('utf-8').strip())
        except KeyboardInterrupt:
            logger.info('Keyboard interrupt detected, killing process in '
                        'container.')
            _, kill_log = container.exec_run(
                cmd='/bin/bash -c "pgrep sky_run.sh | xargs kill"',
                stdout=True,
                stderr=True,
                stream=True,
                privileged=True)
            for line in kill_log:
                logger.info(line.decode('utf-8').strip())
