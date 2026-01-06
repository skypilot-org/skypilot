"""Task: a coarse-grained stage in an application."""
import collections
import json
import os
import re
from typing import (Any, Callable, Dict, Iterable, List, Optional, Set, Tuple,
                    Union)

import colorama
from pydantic import SecretStr

from sky import clouds
from sky import dag as dag_lib
from sky import exceptions
from sky import resources as resources_lib
from sky import sky_logging
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.provision import docker_utils
from sky.serve import service_spec
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import git
from sky.utils import registry
from sky.utils import schemas
from sky.utils import ux_utils
from sky.utils import volume as volume_lib
from sky.utils import yaml_utils

logger = sky_logging.init_logger(__name__)

_VALID_NAME_REGEX = '[a-zA-Z0-9]+(?:[._-]{1,2}[a-zA-Z0-9]+)*'
_VALID_NAME_DESCR = ('ASCII characters and may contain lowercase and'
                     ' uppercase letters, digits, underscores, periods,'
                     ' and dashes. Must start and end with alphanumeric'
                     ' characters. No triple dashes or underscores.')

_RUN_FN_CHECK_FAIL_MSG = (
    'run command generator must take exactly 2 arguments: node_rank (int) and'
    'a list of node ip addresses (List[str]). Got {run_sig}')


def _is_valid_name(name: Optional[str]) -> bool:
    """Checks if the task name is valid.

    Valid is defined as either NoneType or str with ASCII characters which may
    contain lowercase and uppercase letters, digits, underscores, periods,
    and dashes. Must start and end with alphanumeric characters.
    No triple dashes or underscores.

    Examples:
        some_name_here
        some-name-here
        some__name__here
        some--name--here
        some__name--here
        some.name.here
        some-name_he.re
        this---shouldnt--work
        this___shouldnt_work
        _thisshouldntwork
        thisshouldntwork_
    """
    if name is None:
        return True
    return bool(re.fullmatch(_VALID_NAME_REGEX, name))


def _fill_in_env_vars(
    yaml_field: Dict[str, Any],
    task_envs: Dict[str, str],
) -> Dict[str, Any]:
    """Detects env vars in yaml field and fills them with task_envs.

    Use cases of env vars in file_mounts:
    - dst/src paths; e.g.,
        /model_path/llama-${SIZE}b: s3://llama-weights/llama-${SIZE}b
    - storage's name (bucket name)
    - storage's source (local path)

    Use cases of env vars in service:
    - model type; e.g.,
        service:
          readiness_probe:
            path: /v1/chat/completions
            post_data:
              model: $MODEL_NAME
              messages:
                - role: user
                  content: How to print hello world?
              max_tokens: 1

    We simply dump yaml_field into a json string, and replace env vars using
    regex. This should be safe as yaml config has been schema-validated.

    Env vars of the following forms are detected:
        - ${ENV}
        - $ENV
    where <ENV> must appear in task.envs.
    """
    # TODO(zongheng): support ${ENV:-default}?
    yaml_field_str = json.dumps(yaml_field)

    def replace_var(match):
        var_name = match.group(1)
        # If the variable isn't in the dictionary, return it unchanged
        return task_envs.get(var_name, match.group(0))

    # Pattern for valid env var names in bash.
    pattern = r'\$\{?\b([a-zA-Z_][a-zA-Z0-9_]*)\b\}?'
    yaml_field_str = re.sub(pattern, replace_var, yaml_field_str)
    return json.loads(yaml_field_str)


def _check_docker_login_config(task_envs: Dict[str, str],
                               task_secrets: Dict[str, SecretStr]) -> bool:
    """Validates a valid docker login config in task_envs and task_secrets.

    Docker login variables must be specified together either in envs OR secrets,
    not split across both. If any of the docker login env vars is set, all of
    them must be set in the same location.

    Args:
        task_envs: Environment variables
        task_secrets: Secret variables (optional, defaults to empty dict)

    Returns:
        True if there is a valid docker login config.
        False otherwise.
    Raises:
        ValueError: if docker login configuration is invalid.
    """
    if task_secrets is None:
        task_secrets = {}

    all_keys = constants.DOCKER_LOGIN_ENV_VARS
    envs_keys = all_keys & set(task_envs.keys())
    secrets_keys = all_keys & set(task_secrets.keys())

    # Check if any docker variables exist
    if not envs_keys and not secrets_keys:
        return False

    # Check if variables are split across envs and secrets
    if envs_keys and secrets_keys:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                'Docker login variables must be specified together either '
                'in envs OR secrets, not split across both. '
                f'Found in envs: {sorted(envs_keys)}, '
                f'Found in secrets: {sorted(secrets_keys)}')

    # Check if all variables are present in the chosen location
    if envs_keys:
        if len(envs_keys) != len(all_keys):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Docker login variables must be specified together '
                    'in envs. '
                    f'Missing from envs: {sorted(all_keys - envs_keys)}')

    if secrets_keys:
        if len(secrets_keys) != len(all_keys):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Docker login variables must be specified together '
                    'in secrets. '
                    f'Missing from secrets: {sorted(all_keys - secrets_keys)}')

    return True


def _with_docker_login_config(
    resources: Union[Set['resources_lib.Resources'],
                     List['resources_lib.Resources']],
    task_envs: Dict[str, str],
    task_secrets: Dict[str, SecretStr],
) -> Union[Set['resources_lib.Resources'], List['resources_lib.Resources']]:
    if not _check_docker_login_config(task_envs, task_secrets):
        return resources
    envs = task_envs.copy()
    for key, value in task_secrets.items():
        envs[key] = value.get_secret_value()
    docker_login_config = docker_utils.DockerLoginConfig.from_env_vars(envs)

    def _add_docker_login_config(resources: 'resources_lib.Resources'):
        docker_image = resources.extract_docker_image()
        if docker_image is None:
            logger.warning(f'{colorama.Fore.YELLOW}Docker login configs '
                           f'{", ".join(constants.DOCKER_LOGIN_ENV_VARS)} '
                           'are provided, but no docker image is specified '
                           'in `image_id`. The login configs will be '
                           f'ignored.{colorama.Style.RESET_ALL}')
            return resources
        # Already checked in extract_docker_image
        assert resources.image_id is not None and len(
            resources.image_id) == 1, resources.image_id
        region = list(resources.image_id.keys())[0]
        return resources.copy(image_id={region: 'docker:' + docker_image},
                              _docker_login_config=docker_login_config)

    new_resources = []
    for r in resources:
        new_resources.append(_add_docker_login_config(r))
    return type(resources)(new_resources)


def _with_docker_username_for_runpod(
    resources: Union[Set['resources_lib.Resources'],
                     List['resources_lib.Resources']],
    task_envs: Dict[str, str],
    task_secrets: Dict[str, SecretStr],
) -> Union[Set['resources_lib.Resources'], List['resources_lib.Resources']]:
    envs = task_envs.copy()
    for key, value in task_secrets.items():
        envs[key] = value.get_secret_value()
    docker_username_for_runpod = envs.get(
        constants.RUNPOD_DOCKER_USERNAME_ENV_VAR)

    # We should not call r.copy() if docker_username_for_runpod is None,
    # to prevent `DummyResources` instance becoming a `Resources` instance.
    if docker_username_for_runpod is None:
        return resources
    return (type(resources)(
        r.copy(_docker_username_for_runpod=docker_username_for_runpod)
        for r in resources))


def get_plaintext_envs_and_secrets(
    envs_and_secrets: Dict[str, Union[str, SecretStr]],) -> Dict[str, str]:
    return {
        k: v.get_secret_value() if isinstance(v, SecretStr) else v
        for k, v in envs_and_secrets.items()
    }


def get_plaintext_secrets(secrets: Dict[str, SecretStr]) -> Dict[str, str]:
    return {k: v.get_secret_value() for k, v in secrets.items()}


class Task:
    """Task: a computation to be run on the cloud."""

    def __init__(
        self,
        name: Optional[str] = None,
        *,
        setup: Optional[Union[str, List[str]]] = None,
        run: Optional[Union[str, List[str]]] = None,
        envs: Optional[Dict[str, str]] = None,
        secrets: Optional[Dict[str, str]] = None,
        workdir: Optional[Union[str, Dict[str, Any]]] = None,
        num_nodes: Optional[int] = None,
        file_mounts: Optional[Dict[str, str]] = None,
        storage_mounts: Optional[Dict[str, storage_lib.Storage]] = None,
        volumes: Optional[Dict[str, Union[str, Dict[str, Any]]]] = None,
        resources: Optional[Union['resources_lib.Resources',
                                  List['resources_lib.Resources'],
                                  Set['resources_lib.Resources']]] = None,
        # Advanced:
        docker_image: Optional[str] = None,
        event_callback: Optional[str] = None,
        blocked_resources: Optional[Iterable['resources_lib.Resources']] = None,
        # Internal use only.
        _file_mounts_mapping: Optional[Dict[str, str]] = None,
        _volume_mounts: Optional[List[volume_lib.VolumeMount]] = None,
        _metadata: Optional[Dict[str, Any]] = None,
        _user_specified_yaml: Optional[str] = None,
    ):
        """Initializes a Task.

        All fields are optional.  ``Task.run`` is the actual program: either a
        shell command to run (str) or a command generator for different nodes
        (lambda; see below).

        Optionally, call ``Task.set_resources()`` to set the resource
        requirements for this task.  If not set, a default CPU-only requirement
        is assumed (the same as ``sky launch``).

        All setters of this class, ``Task.set_*()``, return ``self``, i.e.,
        they are fluent APIs and can be chained together.

        Example:
            .. code-block:: python

                # A Task that will sync up local workdir '.', containing
                # requirements.txt and train.py.
                sky.Task(setup='pip install requirements.txt',
                         run='python train.py',
                         workdir='.')

                # An empty Task for provisioning a cluster.
                task = sky.Task(num_nodes=n).set_resources(...)

                # Chaining setters.
                sky.Task().set_resources(...).set_file_mounts(...)

        Args:
          name: A string name for the Task for display purposes.
          setup: A setup command(s), which will be run before executing the run
            commands ``run``, and executed under ``workdir``.
          run: The actual command for the task. If not None, either a shell
            command(s) (str, list(str)) or a command generator (callable). If
            latter, it must take a node rank and a list of node addresses as
            input and return a shell command (str) (valid to return None for
            some nodes, in which case no commands are run on them). Run
            commands will be run under ``workdir``. Note the command generator
            should be a self-contained lambda.
          envs: A dictionary of environment variables to set before running the
            setup and run commands.
          secrets: A dictionary of secret environment variables to set before
            running the setup and run commands. These will be redacted in logs
            and YAML output.
          workdir: The local working directory or a git repository.
            For a local working directory, this directory will be synced
            to a location on the remote VM(s), and ``setup`` and ``run``
            commands will be run under that location (thus, they can rely on
            relative paths when invoking binaries).
            If a git repository is provided, the repository will be cloned to
            the working directory and the ``setup`` and ``run`` commands will
            be run under the cloned repository.
          num_nodes: The number of nodes to provision for this Task.  If None,
            treated as 1 node.  If > 1, each node will execute its own
            setup/run command, where ``run`` can either be a str, meaning all
            nodes get the same command, or a lambda, with the semantics
            documented above.
          file_mounts: An optional dict of ``{remote_path: (local_path|cloud
            URI)}``, where remote means the VM(s) on which this Task will
            eventually run on, and local means the node from which the task is
            launched.
          storage_mounts: an optional dict of ``{mount_path: sky.Storage
            object}``, where mount_path is the path inside the remote VM(s)
            where the Storage object will be mounted on.
          volumes: A dict of volumes to be mounted for the task. The dict has
            the form of ``{mount_path: volume_name}`` for external persistent
            volumes, or ``{mount_path: volume_config}`` for ephemeral volumes
            where volume_config is a dict with 'size', and optional type,
            labels, and 'config' fields, etc.
          resources: either a sky.Resources, a set of them, or a list of them.
            A set or a list of resources asks the optimizer to "pick the
            best of these resources" to run this task.
          docker_image: (EXPERIMENTAL: Only in effect when LocalDockerBackend
            is used.) The base docker image that this Task will be built on.
            Defaults to 'gpuci/miniforge-cuda:11.4-devel-ubuntu18.04'.
          event_callback: A bash script that will be executed when the task
            changes state.
          blocked_resources: A set of resources that this task cannot run on.
          _file_mounts_mapping: (Internal use only) A dictionary of file mounts
            mapping.
          _volume_mounts: (Internal use only) A list of volume mounts.
          _metadata: (Internal use only) A dictionary of metadata to be added to
            the task.
          _user_specified_yaml: (Internal use only) A string of user-specified
            YAML config.
        """
        self.name = name
        self.storage_mounts: Dict[str, storage_lib.Storage] = {}
        self.storage_plans: Dict[storage_lib.Storage,
                                 storage_lib.StoreType] = {}
        self._envs = envs or {}
        self._secrets = {}
        if secrets is not None:
            self._secrets = {k: SecretStr(v) for k, v in secrets.items()}
        self._volumes = volumes or {}

        # concatenate commands if given as list
        def _concat(commands: Optional[Union[str, List[str]]]) -> Optional[str]:
            if isinstance(commands, list):
                return '\n'.join(commands)
            return commands

        self.run = _concat(run)
        self.setup = _concat(setup)

        # Validate Docker login configuration early if both envs and secrets
        # contain Docker variables
        if self._envs or self._secrets:
            _check_docker_login_config(self._envs, self._secrets)

        self.workdir = workdir
        self.docker_image = (docker_image if docker_image else
                             'gpuci/miniforge-cuda:11.4-devel-ubuntu18.04')
        self.event_callback = event_callback
        # Ignore type error due to a mypy bug.
        # https://github.com/python/mypy/issues/3004
        self._num_nodes = 1
        self.num_nodes = num_nodes  # type: ignore

        self.inputs: Optional[str] = None
        self.outputs: Optional[str] = None
        self.estimated_inputs_size_gigabytes: Optional[float] = None
        self.estimated_outputs_size_gigabytes: Optional[float] = None
        # Default to CPU VM
        self.resources: Union[List['resources_lib.Resources'],
                              Set['resources_lib.Resources']] = {
                                  resources_lib.Resources()
                              }
        self._service: Optional[service_spec.SkyServiceSpec] = None

        # Resources that this task cannot run on.
        self.blocked_resources = blocked_resources

        self.time_estimator_func: Optional[Callable[['resources_lib.Resources'],
                                                    int]] = None
        self.file_mounts: Optional[Dict[str, str]] = None

        # Only set when 'self' is a jobs controller task: 'self.managed_job_dag'
        # is the underlying managed job dag (sky.Dag object).
        self.managed_job_dag: Optional['dag_lib.Dag'] = None

        # Only set when 'self' is a sky serve controller task.
        self.service_name: Optional[str] = None

        # Filled in by the optimizer.  If None, this Task is not planned.
        self.best_resources: Optional['resources_lib.Resources'] = None

        # For internal use only.
        self.file_mounts_mapping: Optional[Dict[str,
                                                str]] = _file_mounts_mapping
        self.volume_mounts: Optional[List[volume_lib.VolumeMount]] = (
            _volume_mounts)

        self._metadata = _metadata if _metadata is not None else {}

        if resources is not None:
            self.set_resources(resources)
        if storage_mounts is not None:
            self.set_storage_mounts(storage_mounts)
        if file_mounts is not None:
            self.set_file_mounts(file_mounts)

        dag = dag_lib.get_current_dag()
        if dag is not None:
            dag.add(self)

        self._user_specified_yaml = _user_specified_yaml

    def validate(self,
                 skip_file_mounts: bool = False,
                 skip_workdir: bool = False):
        """Validate all fields of the task.

        Args:
            skip_file_mounts: Whether to skip validating file mounts.
            skip_workdir: Whether to skip validating workdir.
        """
        self.validate_name()
        self.validate_run()
        if not skip_workdir:
            self.expand_and_validate_workdir()
        if not skip_file_mounts:
            self.expand_and_validate_file_mounts()
        for r in self.resources:
            r.validate()

    def validate_name(self):
        """Validates if the task name is valid."""
        if not _is_valid_name(self.name):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Invalid task name {self.name}. Valid name: '
                                 f'{_VALID_NAME_DESCR}')

    def validate_run(self):
        """Validates if the run command is valid."""
        if self.run is not None and not isinstance(self.run, str):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('run must be a shell script (str). '
                                 f'Got {type(self.run)}')

    def expand_and_validate_file_mounts(self):
        """Expand file_mounts paths to absolute paths and validate them.

        Note: if this function is called on a remote SkyPilot API server,
        it must be after the client side has sync-ed all files to the
        remote server.
        """
        if self.file_mounts is None:
            return
        for target, source in self.file_mounts.items():
            location = f'file_mounts.{target}: {source}'
            self._validate_mount_path(target, location)
            self._validate_path(source, location)
            if data_utils.is_cloud_store_url(target):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'File mount destination paths cannot be cloud storage')
            if not data_utils.is_cloud_store_url(source):
                self.file_mounts[target] = os.path.abspath(
                    os.path.expanduser(source))
                if not os.path.exists(self.file_mounts[target]
                                     ) and not source.startswith('skypilot:'):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'File mount source {source!r} does not exist '
                            'locally. To fix: check if it exists, and correct '
                            'the path.')

    def _validate_mount_path(self, path: str, location: str):
        self._validate_path(path, location)
        # TODO(zhwu): /home/username/sky_workdir as the target path need
        # to be filtered out as well.
        if (path == constants.SKY_REMOTE_WORKDIR and self.workdir is not None):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Cannot use {constants.SKY_REMOTE_WORKDIR!r} as a '
                    'destination path of a file mount, as it will be used '
                    'by the workdir. If uploading a file/folder to the '
                    'workdir is needed, please specify the full path to '
                    'the file/folder.')

    def _validate_path(self, path: str, location: str):
        if path.endswith('/'):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Mount paths cannot end with a slash '
                                 f'Found: {path} in {location}')

    def expand_and_validate_workdir(self):
        """Expand workdir to absolute path and validate it.

        Note: if this function is called on a remote SkyPilot API server,
        it must be after the client side has sync-ed all files to the
        remote server.
        """
        if self.workdir is None:
            return
        # Only expand the workdir if it is a string
        if isinstance(self.workdir, dict):
            git_ref = self.workdir.get('ref')
            if git_ref is not None:
                self._metadata['git_commit'] = git_ref
            return
        user_workdir = self.workdir
        self.workdir = os.path.abspath(os.path.expanduser(user_workdir))
        if not os.path.isdir(self.workdir):
            # Symlink to a dir is legal (isdir() follows symlinks).
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Workdir must be a valid directory (or '
                    f'a symlink to a directory). {user_workdir} not found.')

        self._metadata['git_commit'] = common_utils.get_git_commit(self.workdir)

    @staticmethod
    def from_yaml_config(
        config: Dict[str, Any],
        env_overrides: Optional[List[Tuple[str, str]]] = None,
        secrets_overrides: Optional[List[Tuple[str, str]]] = None,
    ) -> 'Task':
        user_specified_yaml = config.pop('_user_specified_yaml',
                                         yaml_utils.dump_yaml_str(config))
        # More robust handling for 'envs': explicitly convert keys and values to
        # str, since users may pass '123' as keys/values which will get parsed
        # as int causing validate_schema() to fail.
        envs = config.get('envs')
        if envs is not None and isinstance(envs, dict):
            new_envs: Dict[str, Optional[str]] = {}
            for k, v in envs.items():
                if v is not None:
                    new_envs[str(k)] = str(v)
                else:
                    new_envs[str(k)] = None
            config['envs'] = new_envs

        # More robust handling for 'secrets': explicitly convert keys and values
        # to str, since users may pass '123' as keys/values which will get
        # parsed as int causing validate_schema() to fail.
        secrets = config.get('secrets')
        if secrets is not None and isinstance(secrets, dict):
            new_secrets: Dict[str, Optional[str]] = {}
            for k, v in secrets.items():
                if v is not None:
                    new_secrets[str(k)] = str(v)
                else:
                    new_secrets[str(k)] = None
            config['secrets'] = new_secrets

        common_utils.validate_schema(config, schemas.get_task_schema(),
                                     'Invalid task YAML: ')
        if env_overrides is not None:
            # We must override env vars before constructing the Task, because
            # the Storage object creation is eager and it (its name/source
            # fields) may depend on env vars.
            #
            # FIXME(zongheng): The eagerness / how we construct Task's from
            # entrypoint (YAML, CLI args) should be fixed.
            new_envs = config.get('envs', {})
            new_envs.update(env_overrides)
            config['envs'] = new_envs

        if secrets_overrides is not None:
            # Override secrets vars from CLI.
            new_secrets = config.get('secrets', {})
            new_secrets.update(secrets_overrides)
            config['secrets'] = new_secrets

        for k, v in config.get('envs', {}).items():
            if v is None:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Environment variable {k!r} is None. Please set a '
                        'value for it in task YAML or with --env flag. '
                        f'To set it to be empty, use an empty string ({k}: "" '
                        f'in task YAML or --env {k}="" in CLI).')

        for k, v in config.get('secrets', {}).items():
            if v is None:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Secret variable {k!r} is None. Please set a '
                        'value for it in task YAML or with --secret flag. '
                        f'To set it to be empty, use an empty string ({k}: "" '
                        f'in task YAML or --secret {k}="" in CLI).')

        # Fill in any Task.envs into file_mounts (src/dst paths, storage
        # name/source).
        env_vars = config.get('envs', {})
        secrets = config.get('secrets', {})
        env_and_secrets = env_vars.copy()
        env_and_secrets.update(secrets)
        if config.get('file_mounts') is not None:
            config['file_mounts'] = _fill_in_env_vars(config['file_mounts'],
                                                      env_and_secrets)

        # Fill in any Task.envs into service (e.g. MODEL_NAME).
        if config.get('service') is not None:
            config['service'] = _fill_in_env_vars(config['service'],
                                                  env_and_secrets)

        # Fill in any Task.envs into workdir
        if config.get('workdir') is not None:
            config['workdir'] = _fill_in_env_vars(config['workdir'],
                                                  env_and_secrets)

        if config.get('volumes') is not None:
            config['volumes'] = _fill_in_env_vars(config['volumes'],
                                                  env_and_secrets)

        task = Task(
            config.pop('name', None),
            run=config.pop('run', None),
            workdir=config.pop('workdir', None),
            setup=config.pop('setup', None),
            num_nodes=config.pop('num_nodes', None),
            envs=config.pop('envs', None),
            secrets=config.pop('secrets', None),
            volumes=config.pop('volumes', None),
            event_callback=config.pop('event_callback', None),
            _file_mounts_mapping=config.pop('file_mounts_mapping', None),
            _metadata=config.pop('_metadata', None),
            _user_specified_yaml=user_specified_yaml,
        )

        # Create lists to store storage objects inlined in file_mounts.
        # These are retained in dicts in the YAML schema and later parsed to
        # storage objects with the storage/storage_mount objects.
        fm_storages = []
        file_mounts = config.pop('file_mounts', None)
        volumes = []
        if file_mounts is not None:
            copy_mounts = {}
            for dst_path, src in file_mounts.items():
                # Check if it is str path
                if isinstance(src, str):
                    copy_mounts[dst_path] = src
                # If the src is not a str path, it is likely a dict. Try to
                # parse storage object.
                elif isinstance(src, dict):
                    if (src.get('store') ==
                            storage_lib.StoreType.VOLUME.value.lower()):
                        # Build the volumes config for resources.
                        volume_config = {
                            'path': dst_path,
                        }
                        if src.get('name'):
                            volume_config['name'] = src.get('name')
                        persistent = src.get('persistent', False)
                        volume_config['auto_delete'] = not persistent
                        volume_config_detail = src.get('config', {})
                        volume_config.update(volume_config_detail)
                        volumes.append(volume_config)
                        source_path = src.get('source')
                        if source_path:
                            # For volume, copy the source path to the
                            # data directory of the volume mount point.
                            copy_mounts[
                                f'{dst_path.rstrip("/")}/data'] = source_path
                    else:
                        fm_storages.append((dst_path, src))
                else:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(f'Unable to parse file_mount '
                                         f'{dst_path}:{src}')
            task.set_file_mounts(copy_mounts)

        task_storage_mounts: Dict[str, storage_lib.Storage] = {}
        all_storages = fm_storages
        for storage in all_storages:
            mount_path = storage[0]
            assert mount_path, 'Storage mount path cannot be empty.'
            try:
                storage_obj = storage_lib.Storage.from_yaml_config(storage[1])
            except exceptions.StorageSourceError as e:
                # Patch the error message to include the mount path, if included
                e.args = (e.args[0].replace('<destination_path>',
                                            mount_path),) + e.args[1:]
                raise e
            task_storage_mounts[mount_path] = storage_obj
        task.set_storage_mounts(task_storage_mounts)

        if config.get('inputs') is not None:
            inputs_dict = config.pop('inputs')
            assert len(inputs_dict) == 1, 'Only one input is allowed.'
            inputs = list(inputs_dict.keys())[0]
            estimated_size_gigabytes = list(inputs_dict.values())[0]
            # TODO: allow option to say (or detect) no download/egress cost.
            task.set_inputs(inputs=inputs,
                            estimated_size_gigabytes=estimated_size_gigabytes)

        if config.get('outputs') is not None:
            outputs_dict = config.pop('outputs')
            assert len(outputs_dict) == 1, 'Only one output is allowed.'
            outputs = list(outputs_dict.keys())[0]
            estimated_size_gigabytes = list(outputs_dict.values())[0]
            task.set_outputs(outputs=outputs,
                             estimated_size_gigabytes=estimated_size_gigabytes)

        # Handle the top-level config field
        config_override = config.pop('config', None)

        # Store the final config override for use in resource setup
        cluster_config_override = config_override

        # Parse resources field.
        resources_config = config.pop('resources', {})
        if cluster_config_override is not None:
            assert resources_config.get('_cluster_config_overrides') is None, (
                'Cannot set _cluster_config_overrides in both resources and '
                'experimental.config_overrides')
            resources_config[
                '_cluster_config_overrides'] = cluster_config_override
        if volumes:
            resources_config['volumes'] = volumes
        task.set_resources(
            resources_lib.Resources.from_yaml_config(resources_config))

        service = config.pop('service', None)
        pool = config.pop('pool', None)
        if service is not None and pool is not None:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Cannot set both service and pool in the same task.')

        if service is not None:
            service = service_spec.SkyServiceSpec.from_yaml_config(service)
            task.set_service(service)
        elif pool is not None:
            # When pool is a dict (from top-level pool: in YAML), wrap it
            # properly The schema expects {'pool': {...}} structure, not
            # {'workers': 1, 'pool': True}
            if isinstance(pool, dict):
                # pool is a dict like {'workers': 1, 'max_workers': 3}
                # Wrap it as {'pool': {'workers': 1, 'max_workers': 3}}
                pool_config_dict = {'pool': pool}
            else:
                # pool is a boolean True (shouldn't happen, but handle it)
                pool_config_dict = {'pool': {}}
            pool_spec = service_spec.SkyServiceSpec.from_yaml_config(
                pool_config_dict)
            task.set_service(pool_spec)

        volume_mounts = config.pop('volume_mounts', None)
        if volume_mounts is not None:
            task.volume_mounts = []
            for vol in volume_mounts:
                common_utils.validate_schema(vol,
                                             schemas.get_volume_mount_schema(),
                                             'Invalid volume mount config: ')
                volume_mount = volume_lib.VolumeMount.from_yaml_config(vol)
                task.volume_mounts.append(volume_mount)

        assert not config, f'Invalid task args: {config.keys()}'
        return task

    @staticmethod
    def from_yaml(yaml_path: str) -> 'Task':
        """Initializes a task from a task YAML.

        Example:
            .. code-block:: python

                task = sky.Task.from_yaml('/path/to/task.yaml')

        Args:
          yaml_path: file path to a valid task yaml file.

        Raises:
          ValueError: if the path gets loaded into a str instead of a dict; or
            if there are any other parsing errors.
        """
        with open(os.path.expanduser(yaml_path), 'r', encoding='utf-8') as f:
            # TODO(zongheng): use
            #  https://github.com/yaml/pyyaml/issues/165#issuecomment-430074049
            # to raise errors on duplicate keys.
            user_specified_yaml = f.read()
            return Task.from_yaml_str(user_specified_yaml)

    @staticmethod
    def from_yaml_str(yaml_str: str) -> 'Task':
        """Initializes a task from a task YAML string.

        Example:
            .. code-block:: python

                task = sky.Task.from_yaml_str('yaml_str')
        """
        config = yaml_utils.safe_load(yaml_str)

        if isinstance(config, str):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('YAML loaded as str, not as dict. '
                                 f'Is it correct? content:\n{yaml_str}')

        if config is None:
            config = {}
        config['_user_specified_yaml'] = yaml_str
        return Task.from_yaml_config(config)

    def resolve_and_validate_volumes(self) -> None:
        """Resolve volumes config to volume mounts and validate them.

        Raises:
            exceptions.VolumeNotFoundError: if any volume is not found.
            exceptions.VolumeTopologyConflictError: if there is conflict in the
              volumes and compute topology.
        """
        # Volumes has been resolved, a typical case is that the API server
        # has resolved the volumes and the dag was then submitted to
        # controllers.
        if self.volume_mounts is not None:
            return None
        if not self._volumes:
            return None
        volume_mounts: List[volume_lib.VolumeMount] = []
        for dst_path, vol in self._volumes.items():
            self._validate_mount_path(dst_path, location='volumes')
            # Shortcut for `dst_path: volume_name` (external persistent volume)
            if isinstance(vol, str):
                volume_mount = volume_lib.VolumeMount.resolve(dst_path, vol)
            elif isinstance(vol, dict):
                # Check if this is an ephemeral volume config or external volume
                # with 'size' field
                if 'size' in vol:
                    # This is an ephemeral volume config
                    volume_mount = (
                        volume_lib.VolumeMount.resolve_ephemeral_config(
                            dst_path, vol))
                elif 'name' in vol:
                    # External volume with 'name' field
                    volume_mount = volume_lib.VolumeMount.resolve(
                        dst_path, vol['name'])
                else:
                    raise ValueError(
                        f'Invalid volume config: {dst_path}: {vol}. '
                        'Either "size" (for ephemeral volume) or "name" '
                        '(for external volume) must be set.')
            else:
                raise ValueError(f'Invalid volume config: {dst_path}: {vol}')
            volume_mounts.append(volume_mount)
        # Disable certain access modes
        disabled_modes = {}
        if self.num_nodes > 1:
            disabled_modes[
                volume_lib.VolumeAccessMode.READ_WRITE_ONCE.value] = (
                    'access mode ReadWriteOnce is not supported for '
                    'multi-node tasks.')
            disabled_modes[
                volume_lib.VolumeAccessMode.READ_WRITE_ONCE_POD.value] = (
                    'access mode ReadWriteOncePod is not supported for '
                    'multi-node tasks.')
        # TODO(aylei): generalize access mode to all volume types
        # Record the required topology and the volume that requires it, e.g.
        # {'cloud': ('volume_name', 'aws')}
        topology: Dict[str, Tuple[str, Optional[str]]] = {
            'cloud': ('', None),
            'region': ('', None),
            'zone': ('', None),
        }
        for vol in volume_mounts:
            # Check access mode
            access_mode = vol.volume_config.config.get('access_mode', '')
            if access_mode in disabled_modes:
                raise ValueError(f'Volume {vol.volume_name} with '
                                 f'{disabled_modes[access_mode]}')
            # Skip ephemeral volumes for topology check
            if vol.is_ephemeral:
                continue
            # Check topology
            for key, (vol_name, previous_req) in topology.items():
                req = getattr(vol.volume_config, key)
                if req is not None:
                    if previous_req is not None and req != previous_req:
                        raise exceptions.VolumeTopologyConflictError(
                            f'Volume {vol.volume_name} can only be attached on '
                            f'{key}:{req}, which conflicts with another volume '
                            f'{vol_name} that requires {key}:{previous_req}. '
                            f'Please use different volumes and retry.')
                    topology[key] = (vol.volume_name, req)
        # Now we have the topology requirements from the intersection of all
        # volumes. Check if there is topology conflict with the resources.
        # Volume must have no conflict with ALL resources even if user
        # specifies 'any_of' resources to ensure no resources will conflict
        # with the volumes during failover.

        for res in self.resources:
            for key, (vol_name, vol_req) in topology.items():
                req = getattr(res, key)
                if (req is not None and vol_req is not None and
                        str(req) != vol_req):
                    raise exceptions.VolumeTopologyConflictError(
                        f'The task requires {key}:{req}, which conflicts with '
                        f'the volume constraint {key}:{vol_req}. Please '
                        f'use different volumes and retry.')
        # No topology conflict, we safely override the topology of resources to
        # satisfy the volume constraints.
        override_params = {}
        for key, (vol_name, vol_req) in topology.items():
            if vol_req is not None:
                if key == 'cloud':
                    override_params[key] = registry.CLOUD_REGISTRY.from_str(
                        vol_req)
                else:
                    override_params[key] = vol_req
        logger.debug(
            f'Override resources with volume constraints: {override_params}')
        self.set_resources_override(override_params)
        self.volume_mounts = volume_mounts

    @property
    def num_nodes(self) -> int:
        return self._num_nodes

    @num_nodes.setter
    def num_nodes(self, num_nodes: Optional[int]) -> None:
        if num_nodes is None:
            num_nodes = 1
        if not isinstance(num_nodes, int) or num_nodes <= 0:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'num_nodes should be a positive int. Got: {num_nodes}')
        self._num_nodes = num_nodes

    @property
    def metadata(self) -> Dict[str, Any]:
        return self._metadata

    @property
    def metadata_json(self) -> str:
        return json.dumps(self._metadata)

    @property
    def envs(self) -> Dict[str, str]:
        return self._envs

    @property
    def secrets(self) -> Dict[str, SecretStr]:
        return self._secrets

    @property
    def volumes(self) -> Dict[str, Union[str, Dict[str, Any]]]:
        return self._volumes

    def set_volumes(self, volumes: Dict[str, Union[str, Dict[str,
                                                             Any]]]) -> None:
        """Sets the volumes for this task.

        Args:
          volumes: a dict of ``{mount_path: volume_name}`` for external
            persistent volumes, or ``{mount_path: volume_config}`` for
            ephemeral volumes.
        """
        self._volumes = volumes

    def update_volumes(self, volumes: Dict[str, Union[str, Dict[str,
                                                                Any]]]) -> None:
        """Updates the volumes for this task."""
        self._volumes.update(volumes)

    def update_envs(
            self, envs: Union[None, List[Tuple[str, str]],
                              Dict[str, str]]) -> 'Task':
        """Updates environment variables for use inside the setup/run commands.

        Args:
          envs: (optional) either a list of ``(env_name, value)`` or a dict
            ``{env_name: value}``.

        Returns:
          self: The current task, with envs updated.

        Raises:
          ValueError: if various invalid inputs errors are detected.
        """
        if envs is None:
            envs = {}
        if isinstance(envs, (list, tuple)):
            keys = set(env[0] for env in envs)
            if len(keys) != len(envs):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('Duplicate env keys provided.')
            envs = dict(envs)
        if isinstance(envs, dict):
            for key in envs:
                if not isinstance(key, str):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError('Env keys must be strings.')
                if not common_utils.is_valid_env_var(key):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(f'Invalid env key: {key}')
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'envs must be List[Tuple[str, str]] or Dict[str, str]: '
                    f'{envs}')
        self._envs.update(envs)
        # If the update_envs() is called after set_resources(), we need to
        # manually update docker login config in task resources, in case the
        # docker login envs are newly added.
        if _check_docker_login_config(self._envs, self._secrets):
            self.resources = _with_docker_login_config(self.resources,
                                                       self._envs,
                                                       self._secrets)
        self.resources = _with_docker_username_for_runpod(
            self.resources, self._envs, self._secrets)
        return self

    def update_secrets(
            self, secrets: Union[None, List[Tuple[str, str]],
                                 Dict[str, str]]) -> 'Task':
        """Updates secret env vars for use inside the setup/run commands.

        Args:
          secrets: (optional) either a list of ``(secret_name, value)`` or a
            dict ``{secret_name: value}``.

        Returns:
          self: The current task, with secrets updated.

        Raises:
          ValueError: if various invalid inputs errors are detected.
        """
        if secrets is None:
            secrets = {}
        if isinstance(secrets, (list, tuple)):
            keys = set(secret[0] for secret in secrets)
            if len(keys) != len(secrets):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError('Duplicate secret keys provided.')
            secrets = dict(secrets)
        if isinstance(secrets, dict):
            for key in secrets:
                if not isinstance(key, str):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError('Secret keys must be strings.')
                if not common_utils.is_valid_env_var(key):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(f'Invalid secret key: {key}')
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'secrets must be List[Tuple[str, str]] or Dict[str, str]: '
                    f'{secrets}')
        for key, value in secrets.items():
            self._secrets[key] = SecretStr(value)
        # Validate Docker login configuration if needed
        if _check_docker_login_config(self._envs, self._secrets):
            self.resources = _with_docker_login_config(self.resources,
                                                       self._envs,
                                                       self._secrets)
        self.resources = _with_docker_username_for_runpod(
            self.resources, self._envs, self._secrets)
        return self

    @property
    def use_spot(self) -> bool:
        return any(r.use_spot for r in self.resources)

    @property
    def envs_and_secrets(self) -> Dict[str, Union[str, SecretStr]]:
        envs = self.envs.copy()
        envs.update(self.secrets)
        return envs

    def set_inputs(self, inputs: str,
                   estimated_size_gigabytes: float) -> 'Task':
        # E.g., 's3://bucket', 'gs://bucket', or None.
        self.inputs = inputs
        self.estimated_inputs_size_gigabytes = estimated_size_gigabytes
        return self

    def get_inputs(self) -> Optional[str]:
        return self.inputs

    def get_estimated_inputs_size_gigabytes(self) -> Optional[float]:
        return self.estimated_inputs_size_gigabytes

    def get_inputs_cloud(self):
        """EXPERIMENTAL: Returns the cloud my inputs live in."""
        assert isinstance(self.inputs, str), self.inputs
        if self.inputs.startswith('s3:'):
            return clouds.AWS()
        elif self.inputs.startswith('gs:'):
            return clouds.GCP()
        elif self.inputs.startswith('cos:'):
            return clouds.IBM()
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'cloud path not supported: {self.inputs}')

    def set_outputs(self, outputs: str,
                    estimated_size_gigabytes: float) -> 'Task':
        self.outputs = outputs
        self.estimated_outputs_size_gigabytes = estimated_size_gigabytes
        return self

    def get_outputs(self) -> Optional[str]:
        return self.outputs

    def get_estimated_outputs_size_gigabytes(self) -> Optional[float]:
        return self.estimated_outputs_size_gigabytes

    @staticmethod
    def _ensure_consistent_priority(
        resources: Union[List['resources_lib.Resources'],
                         Set['resources_lib.Resources']]
    ) -> None:
        priority = None
        for r in resources:
            if r.priority is None:
                continue
            if priority is None:
                priority = r.priority
            else:
                if priority != r.priority:
                    raise ValueError('Priority is not consistent '
                                     f'across resources: {resources}')

    def set_resources(
        self, resources: Union['resources_lib.Resources',
                               List['resources_lib.Resources'],
                               Set['resources_lib.Resources'], Dict[str, Any]]
    ) -> 'Task':
        """Sets the required resources to execute this task.

        If this function is not called for a Task, default resource
        requirements will be used (8 vCPUs).

        Args:
          resources: either a sky.Resources, a set of them, or a list of them.
            A set or a list of resources asks the optimizer to "pick the
            best of these resources" to run this task.
        Returns:
          self: The current task, with resources set.
        """
        if isinstance(resources, dict):
            resources = resources_lib.Resources.from_yaml_config(resources)
        elif isinstance(resources, resources_lib.Resources):
            resources = {resources}
        self._ensure_consistent_priority(resources)
        # TODO(woosuk): Check if the resources are None.
        self.resources = _with_docker_login_config(resources, self.envs,
                                                   self.secrets)
        # Only have effect on RunPod.
        self.resources = _with_docker_username_for_runpod(
            self.resources, self.envs, self.secrets)

        # Evaluate if the task requires FUSE and set the requires_fuse flag
        for _, storage_obj in self.storage_mounts.items():
            if storage_obj.mode in storage_lib.MOUNTABLE_STORAGE_MODES:
                for r in self.resources:
                    r.set_requires_fuse(True)
                break

        return self

    def set_resources_override(self, override_params: Dict[str, Any]) -> 'Task':
        """Sets the override parameters for the resources."""
        new_resources_list = []
        for res in list(self.resources):
            new_resources = res.copy(**override_params)
            new_resources_list.append(new_resources)

        self.set_resources(type(self.resources)(new_resources_list))
        return self

    def get_resource_config(self) -> Dict[str, Any]:
        return _resources_to_config(self.resources,
                                    factor_out_common_fields=True)

    @property
    def service(self) -> Optional[service_spec.SkyServiceSpec]:
        return self._service

    def set_service(self,
                    service: Optional[service_spec.SkyServiceSpec]) -> 'Task':
        """Sets the service spec for this task.

        Args:
          service: a SkyServiceSpec object.

        Returns:
          self: The current task, with service set.
        """
        self._service = service
        return self

    def set_time_estimator(
            self, func: Callable[['resources_lib.Resources'], int]) -> 'Task':
        """Sets a func mapping resources to estimated time (secs).

        This is EXPERIMENTAL.
        """
        self.time_estimator_func = func
        return self

    def estimate_runtime(self, resources):
        """Returns a func mapping resources to estimated time (secs).

        This is EXPERIMENTAL.
        """
        if self.time_estimator_func is None:
            raise NotImplementedError(
                f'Node [{self}] does not have a cost model set; '
                'call set_time_estimator() first')
        return self.time_estimator_func(resources)

    def set_file_mounts(self, file_mounts: Optional[Dict[str, str]]) -> 'Task':
        """Sets the file mounts for this task.

        Useful for syncing datasets, dotfiles, etc.

        File mounts are a dictionary: ``{remote_path: local_path/cloud URI}``.
        Local (or cloud) files/directories will be synced to the specified
        paths on the remote VM(s) where this Task will run.

        Neither source or destimation paths can end with a slash.

        Example:
            .. code-block:: python

                task.set_file_mounts({
                    '~/.dotfile': '/local/.dotfile',
                    # /remote/dir/ will contain the contents of /local/dir/.
                    '/remote/dir': '/local/dir',
                })

        Args:
          file_mounts: an optional dict of ``{remote_path: local_path/cloud
            URI}``, where remote means the VM(s) on which this Task will
            eventually run on, and local means the node from which the task is
            launched.

        Returns:
          self: the current task, with file mounts set.
        """
        self.file_mounts = file_mounts
        return self

    def update_file_mounts(self, file_mounts: Dict[str, str]) -> 'Task':
        """Updates the file mounts for this task.

        Different from set_file_mounts(), this function updates into the
        existing file_mounts (calls ``dict.update()``), rather than
        overwriting it.

        This should be called before provisioning in order to take effect.

        Example:
            .. code-block:: python

                task.update_file_mounts({
                    '~/.config': '~/Documents/config',
                    '/tmp/workdir': '/local/workdir/cnn-cifar10',
                })

        Args:
          file_mounts: a dict of ``{remote_path: local_path/cloud URI}``, where
            remote means the VM(s) on which this Task will eventually run on,
            and local means the node from which the task is launched.

        Returns:
          self: the current task, with file mounts updated.

        Raises:
          ValueError: if input paths are invalid.
        """
        if self.file_mounts is None:
            self.file_mounts = {}
        assert self.file_mounts is not None
        self.file_mounts.update(file_mounts)
        self.expand_and_validate_file_mounts()
        return self

    def set_storage_mounts(
        self,
        storage_mounts: Optional[Dict[str, storage_lib.Storage]],
    ) -> 'Task':
        """Sets the storage mounts for this task.

        Storage mounts are a dictionary: ``{mount_path: sky.Storage object}``,
        each of which mounts a sky.Storage object (a cloud object store bucket)
        to a path inside the remote cluster.

        A sky.Storage object can be created by uploading from a local directory
        (setting ``source``), or backed by an existing cloud bucket (setting
        ``name`` to the bucket name; or setting ``source`` to the bucket URI).

        Example:
            .. code-block:: python

                task.set_storage_mounts({
                    '/remote/imagenet/': sky.Storage(name='my-bucket',
                                                     source='/local/imagenet'),
                })

        Args:
          storage_mounts: an optional dict of ``{mount_path: sky.Storage
            object}``, where mount_path is the path inside the remote VM(s)
            where the Storage object will be mounted on.

        Returns:
          self: The current task, with storage mounts set.

        Raises:
          ValueError: if input paths are invalid.
        """
        if storage_mounts is None:
            self.storage_mounts = {}
            # Clear the requires_fuse flag if no storage mounts are set.
            for r in self.resources:
                r.set_requires_fuse(False)
            return self
        for target, storage_obj in storage_mounts.items():
            # TODO(zhwu): /home/username/sky_workdir as the target path need
            # to be filtered out as well.
            if (target == constants.SKY_REMOTE_WORKDIR and
                    self.workdir is not None):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        f'Cannot use {constants.SKY_REMOTE_WORKDIR!r} as a '
                        'destination path of a file mount, as it will be used '
                        'by the workdir. If uploading a file/folder to the '
                        'workdir is needed, please specify the full path to '
                        'the file/folder.')

            if data_utils.is_cloud_store_url(target):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Storage mount destination path cannot be cloud storage'
                    )

            if storage_obj.mode in storage_lib.MOUNTABLE_STORAGE_MODES:
                # If any storage is using MOUNT mode, we need to enable FUSE in
                # the resources.
                for r in self.resources:
                    r.set_requires_fuse(True)
        # Storage source validation is done in Storage object
        self.storage_mounts = storage_mounts
        return self

    def update_storage_mounts(
            self, storage_mounts: Dict[str, storage_lib.Storage]) -> 'Task':
        """Updates the storage mounts for this task.

        Different from set_storage_mounts(), this function updates into the
        existing storage_mounts (calls ``dict.update()``), rather than
        overwriting it.

        This should be called before provisioning in order to take effect.

        Args:
          storage_mounts: an optional dict of ``{mount_path: sky.Storage
            object}``, where mount_path is the path inside the remote VM(s)
            where the Storage object will be mounted on.

        Returns:
          self: The current task, with storage mounts updated.

        Raises:
          ValueError: if input paths are invalid.
        """
        if not storage_mounts:
            return self
        task_storage_mounts = self.storage_mounts if self.storage_mounts else {}
        task_storage_mounts.update(storage_mounts)
        return self.set_storage_mounts(task_storage_mounts)

    def _get_preferred_store(
            self) -> Tuple[storage_lib.StoreType, Optional[str]]:
        """Returns the preferred store type and region for this task."""
        # TODO(zhwu, romilb): The optimizer should look at the source and
        #  destination to figure out the right stores to use. For now, we
        #  use a heuristic solution to find the store type by the following
        #  order:
        #  1. cloud/region decided in best_resources.
        #  2. cloud/region specified in the task resources.
        #  3. if not specified or the task's cloud does not support storage,
        #     use the first enabled storage cloud with default region.
        # This should be refactored and moved to the optimizer.

        # This check is not needed to support multiple accelerators;
        # We just need to get the storage_cloud.
        # assert len(self.resources) == 1, self.resources
        storage_cloud = None

        enabled_storage_cloud_names = (
            storage_lib.get_cached_enabled_storage_cloud_names_or_refresh(
                raise_if_no_cloud_access=True))

        if self.best_resources is not None:
            storage_cloud = self.best_resources.cloud
            storage_region = self.best_resources.region
        else:
            resources = list(self.resources)[0]
            storage_cloud = resources.cloud
            storage_region = resources.region

        if storage_cloud is not None:
            if str(storage_cloud) not in enabled_storage_cloud_names:
                storage_cloud = None

        storage_cloud_str = None
        if storage_cloud is None:
            storage_cloud_str = enabled_storage_cloud_names[0]
            assert storage_cloud_str is not None, enabled_storage_cloud_names[0]
            storage_region = None  # Use default region in the Store class
        else:
            storage_cloud_str = str(storage_cloud)

        store_type = storage_lib.StoreType.from_cloud(storage_cloud_str)
        return store_type, storage_region

    def sync_storage_mounts(self) -> None:
        """(INTERNAL) Eagerly syncs storage mounts to cloud storage.

        After syncing up, COPY-mode storage mounts are translated into regular
        file_mounts of the form ``{ /remote/path: {s3,gs,..}://<bucket path>
        }``.
        """
        # The same storage can be used multiple times, and we should construct
        # the storage with stores first, so that the storage will be created on
        # the correct cloud.
        name_to_storage = collections.defaultdict(list)
        for storage in self.storage_mounts.values():
            name_to_storage[storage.name].append(storage)
        for storages in name_to_storage.values():
            # Place the storage with most stores first, so that the storage will
            # be created on the correct cloud.
            storage_to_construct = sorted(storages,
                                          key=lambda x: len(x.stores),
                                          reverse=True)
            for storage in storage_to_construct:
                storage.construct()
                assert storage.name is not None, storage
                if not storage.stores:
                    store_type, store_region = self._get_preferred_store()
                    self.storage_plans[storage] = store_type
                    storage.add_store(store_type, store_region)
                else:
                    # We don't need to sync the storage here as if the stores
                    # are not empty, it measn the storage has been synced during
                    # construct() above.
                    # We will download the first store that is added to remote.
                    assert all(store is not None
                               for store in storage.stores.values()), storage
                    self.storage_plans[storage] = list(storage.stores.keys())[0]

        # The following logic converts the storage mounts with COPY mode into
        # inline file mounts with cloud URIs, so that the _execute_file_mounts()
        # in cloud_vm_ray_backend.py can correctly download from the specific
        # cloud storage on the remote cluster.
        # Note that this will cause duplicate destination paths in file_mounts,
        # and storage_mounts, which should be fine as our to_yaml_config() will
        # only dump the storage mount version, i.e. what user specified.
        storage_mounts = self.storage_mounts
        storage_plans = self.storage_plans
        for mnt_path, storage in storage_mounts.items():
            assert storage.name is not None, storage

            if storage.mode == storage_lib.StorageMode.COPY:
                store_type = storage_plans[storage]
                if store_type is storage_lib.StoreType.S3:
                    # TODO: allow for Storage mounting of different clouds
                    if isinstance(storage.source,
                                  str) and storage.source.startswith('s3://'):
                        blob_path = storage.source
                    else:
                        assert storage.name is not None, storage
                        blob_path = 's3://' + storage.name
                    blob_path = storage.get_bucket_sub_path_prefix(blob_path)
                    self.update_file_mounts({
                        mnt_path: blob_path,
                    })
                elif store_type is storage_lib.StoreType.GCS:
                    if isinstance(storage.source,
                                  str) and storage.source.startswith('gs://'):
                        blob_path = storage.source
                    else:
                        assert storage.name is not None, storage
                        blob_path = 'gs://' + storage.name
                    blob_path = storage.get_bucket_sub_path_prefix(blob_path)
                    self.update_file_mounts({
                        mnt_path: blob_path,
                    })
                elif store_type is storage_lib.StoreType.AZURE:
                    if (isinstance(storage.source, str) and
                            data_utils.is_az_container_endpoint(
                                storage.source)):
                        blob_path = storage.source
                    else:
                        assert storage.name is not None, storage
                        store_object = storage.stores[
                            storage_lib.StoreType.AZURE]
                        assert isinstance(store_object,
                                          storage_lib.AzureBlobStore)
                        storage_account_name = store_object.storage_account_name
                        blob_path = data_utils.AZURE_CONTAINER_URL.format(
                            storage_account_name=storage_account_name,
                            container_name=storage.name)
                    blob_path = storage.get_bucket_sub_path_prefix(blob_path)
                    self.update_file_mounts({
                        mnt_path: blob_path,
                    })
                elif store_type is storage_lib.StoreType.R2:
                    if storage.source is not None and not isinstance(
                            storage.source,
                            list) and storage.source.startswith('r2://'):
                        blob_path = storage.source
                    else:
                        blob_path = 'r2://' + storage.name
                    blob_path = storage.get_bucket_sub_path_prefix(blob_path)
                    self.update_file_mounts({
                        mnt_path: blob_path,
                    })
                elif store_type is storage_lib.StoreType.NEBIUS:
                    if storage.source is not None and not isinstance(
                            storage.source,
                            list) and storage.source.startswith('nebius://'):
                        blob_path = storage.source
                    else:
                        blob_path = 'nebius://' + storage.name
                    blob_path = storage.get_bucket_sub_path_prefix(blob_path)
                    self.update_file_mounts({
                        mnt_path: blob_path,
                    })
                elif store_type is storage_lib.StoreType.IBM:
                    if isinstance(storage.source,
                                  str) and storage.source.startswith('cos://'):
                        # source is a cos bucket's uri
                        blob_path = storage.source
                    else:
                        # source is a bucket name.
                        assert storage.name is not None, storage
                        # extract region from rclone.conf
                        cos_region = data_utils.Rclone.get_region_from_rclone(
                            storage.name, data_utils.Rclone.RcloneStores.IBM)
                        blob_path = f'cos://{cos_region}/{storage.name}'
                    blob_path = storage.get_bucket_sub_path_prefix(blob_path)
                    self.update_file_mounts({mnt_path: blob_path})
                elif store_type is storage_lib.StoreType.OCI:
                    if storage.source is not None and not isinstance(
                            storage.source,
                            list) and storage.source.startswith('oci://'):
                        blob_path = storage.source
                    else:
                        blob_path = 'oci://' + storage.name
                    self.update_file_mounts({
                        mnt_path: blob_path,
                    })
                elif store_type is storage_lib.StoreType.COREWEAVE:
                    if storage.source is not None and not isinstance(
                            storage.source,
                            list) and storage.source.startswith('cw://'):
                        blob_path = storage.source
                    else:
                        blob_path = 'cw://' + storage.name
                    self.update_file_mounts({
                        mnt_path: blob_path,
                    })
                else:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(f'Storage Type {store_type} '
                                         'does not exist!')

                # TODO: Delete from storage_mounts, now that the storage is
                # translated into file_mounts. Note: as is, this will break
                # controller_utils.
                # _maybe_translate_local_file_mounts_and_sync_up(), which still
                # needs the storage, but not the file_mounts.

    def get_local_to_remote_file_mounts(self) -> Optional[Dict[str, str]]:
        """Returns file mounts of the form (dst=VM path, src=local path).

        Any cloud object store URIs (gs://, s3://, etc.), either as source or
        destination, are not included.

        INTERNAL: this method is internal-facing.
        """
        if self.file_mounts is None:
            return None
        d = {}
        for k, v in self.file_mounts.items():
            if not data_utils.is_cloud_store_url(
                    k) and not data_utils.is_cloud_store_url(v):
                d[k] = v
        return d

    def is_controller_task(self) -> bool:
        """Returns whether this task is a jobs/serve controller process."""
        return self.managed_job_dag is not None or self.service_name is not None

    def get_cloud_to_remote_file_mounts(self) -> Optional[Dict[str, str]]:
        """Returns file mounts of the form (dst=VM path, src=cloud URL).

        Local-to-remote file mounts are excluded (handled by
        get_local_to_remote_file_mounts()).

        INTERNAL: this method is internal-facing.
        """
        if self.file_mounts is None:
            return None
        d = {}
        for k, v in self.file_mounts.items():
            if not data_utils.is_cloud_store_url(
                    k) and data_utils.is_cloud_store_url(v):
                d[k] = v
        return d

    def update_workdir(self, workdir: Optional[str], git_url: Optional[str],
                       git_ref: Optional[str]) -> 'Task':
        """Updates the task workdir.

        Args:
            workdir: The workdir to update.
            git_url: The git url to update.
            git_ref: The git ref to update.
        """
        if self.workdir is None or isinstance(self.workdir, str):
            if workdir is not None:
                self.workdir = workdir
                return self
            if git_url is not None:
                self.workdir = {}
                self.workdir['url'] = git_url
                if git_ref is not None:
                    self.workdir['ref'] = git_ref
                return self
            return self
        if git_url is not None:
            self.workdir['url'] = git_url
        if git_ref is not None:
            self.workdir['ref'] = git_ref
        return self

    def update_envs_and_secrets_from_workdir(self) -> 'Task':
        """Updates the task envs and secrets from the workdir."""
        if self.workdir is None:
            return self
        if not isinstance(self.workdir, dict):
            return self
        url = self.workdir['url']
        ref = self.workdir.get('ref', '')
        token = os.environ.get(git.GIT_TOKEN_ENV_VAR)
        ssh_key_path = os.environ.get(git.GIT_SSH_KEY_PATH_ENV_VAR)
        try:
            git_repo = git.GitRepo(url, ref, token, ssh_key_path)
            clone_info = git_repo.get_repo_clone_info()
            if clone_info is None:
                return self
            self.envs[git.GIT_URL_ENV_VAR] = clone_info.url
            if ref:
                ref_type = git_repo.get_ref_type()
                if ref_type == git.GitRefType.COMMIT:
                    self.envs[git.GIT_COMMIT_HASH_ENV_VAR] = ref
                elif ref_type == git.GitRefType.BRANCH:
                    self.envs[git.GIT_BRANCH_ENV_VAR] = ref
                elif ref_type == git.GitRefType.TAG:
                    self.envs[git.GIT_TAG_ENV_VAR] = ref
            if clone_info.token is None and clone_info.ssh_key is None:
                return self
            if clone_info.token is not None:
                self.secrets[git.GIT_TOKEN_ENV_VAR] = SecretStr(
                    clone_info.token)
            if clone_info.ssh_key is not None:
                self.secrets[git.GIT_SSH_KEY_ENV_VAR] = SecretStr(
                    clone_info.ssh_key)
        except exceptions.GitError as e:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'{str(e)}') from None
        return self

    def to_yaml_config(self,
                       use_user_specified_yaml: bool = False) -> Dict[str, Any]:
        """Returns a yaml-style dict representation of the task.

        INTERNAL: this method is internal-facing.
        """
        if use_user_specified_yaml:
            if self._user_specified_yaml is None:
                return self._to_yaml_config(redact_secrets=True)
            config = yaml_utils.safe_load(self._user_specified_yaml)
            if config.get('secrets') is not None:
                config['secrets'] = {k: '<redacted>' for k in config['secrets']}
            return config
        return self._to_yaml_config()

    def _to_yaml_config(self, redact_secrets: bool = False) -> Dict[str, Any]:
        config = {}

        def add_if_not_none(key, value, no_empty: bool = False):
            if no_empty and not value:
                return
            if value is not None:
                config[key] = value

        add_if_not_none('name', self.name)

        tmp_resource_config = _resources_to_config(self.resources)

        add_if_not_none('resources', tmp_resource_config)

        if self.service is not None:
            add_if_not_none('service', self.service.to_yaml_config())

        add_if_not_none('num_nodes', self.num_nodes)

        if self.inputs is not None:
            add_if_not_none('inputs',
                            {self.inputs: self.estimated_inputs_size_gigabytes})
        if self.outputs is not None:
            add_if_not_none(
                'outputs',
                {self.outputs: self.estimated_outputs_size_gigabytes})

        add_if_not_none('setup', self.setup)
        add_if_not_none('workdir', self.workdir)
        add_if_not_none('event_callback', self.event_callback)
        add_if_not_none('run', self.run)

        # Add envs without redaction
        add_if_not_none('envs', self.envs, no_empty=True)

        secrets = self.secrets
        if secrets and not redact_secrets:
            secrets = {k: v.get_secret_value() for k, v in secrets.items()}
        elif secrets and redact_secrets:
            secrets = {k: '<redacted>' for k, v in secrets.items()}
        add_if_not_none('secrets', secrets, no_empty=True)

        add_if_not_none('file_mounts', {})

        if self.file_mounts is not None:
            config['file_mounts'].update(self.file_mounts)

        if self.storage_mounts is not None:
            config['file_mounts'].update({
                mount_path: storage.to_yaml_config()
                for mount_path, storage in self.storage_mounts.items()
            })

        add_if_not_none('file_mounts_mapping', self.file_mounts_mapping)
        add_if_not_none('volumes', self.volumes)
        if self.volume_mounts is not None:
            config['volume_mounts'] = [
                volume_mount.to_yaml_config()
                for volume_mount in self.volume_mounts
            ]
        # we manually check if its empty to not clog up the generated yaml
        add_if_not_none('_metadata', self._metadata if self._metadata else None)
        add_if_not_none('_user_specified_yaml', self._user_specified_yaml)
        return config

    def get_required_cloud_features(
            self) -> Set[clouds.CloudImplementationFeatures]:
        """Returns the required features for this task (but not for resources).

        Features required by the resources are checked separately in
        cloud.get_feasible_launchable_resources().

        INTERNAL: this method is internal-facing.
        """
        required_features = set()

        # Multi-node
        if self.num_nodes > 1:
            required_features.add(clouds.CloudImplementationFeatures.MULTI_NODE)

        # Storage mounting
        for _, storage_mount in self.storage_mounts.items():
            if storage_mount.mode in storage_lib.MOUNTABLE_STORAGE_MODES:
                required_features.add(
                    clouds.CloudImplementationFeatures.STORAGE_MOUNTING)
                break

        return required_features

    def __rshift__(self, b):
        dag = dag_lib.get_current_dag()
        if dag is None:
            raise RuntimeError(
                'Cannot use >> operator outside of a DAG context. '
                'Please use "with sky.Dag() as dag:" to create a DAG context.')
        dag.add_edge(self, b)

    def __repr__(self):
        if isinstance(self.run, str):
            run_msg = self.run.replace('\n', '\\n')
            if len(run_msg) > 20:
                run_msg = f'run=\'{run_msg[:20]}...\''
            else:
                run_msg = f'run=\'{run_msg}\''
        elif self.run is None:
            run_msg = 'run=<empty>'
        else:
            run_msg = 'run=<fn>'

        name_str = ''
        if self.name is not None:
            name_str = f'<name={self.name}>'
        s = f'Task{name_str}({run_msg})'
        if self.inputs is not None:
            s += f'\n  inputs: {self.inputs}'
        if self.outputs is not None:
            s += f'\n  outputs: {self.outputs}'
        if self.num_nodes > 1:
            s += f'\n  nodes: {self.num_nodes}'
        if len(self.resources) > 1:
            resources_str = ('{' + ', '.join(
                r.repr_with_region_zone for r in self.resources) + '}')
            s += f'\n  resources: {resources_str}'
        elif (len(self.resources) == 1 and
              not list(self.resources)[0].is_empty()):
            s += (f'\n  resources: '
                  f'{list(self.resources)[0].repr_with_region_zone}')
        else:
            s += '\n  resources: default instances'
        return s


def _resources_to_config(
        resources: Union[List['resources_lib.Resources'],
                         Set['resources_lib.Resources']],
        factor_out_common_fields: bool = False) -> Dict[str, Any]:
    if len(resources) > 1:
        resource_list: List[Dict[str, Union[str, int]]] = []
        for r in resources:
            resource_list.append(r.to_yaml_config())
        group_key = 'ordered' if isinstance(resources, list) else 'any_of'
        if factor_out_common_fields:
            return _factor_out_common_resource_fields(resource_list, group_key)
        return {group_key: resource_list}
    else:
        return list(resources)[0].to_yaml_config()


def _factor_out_common_resource_fields(configs: List[Dict[str, Union[str,
                                                                     int]]],
                                       group_key: str) -> Dict[str, Any]:
    """Factors out the fields that are common to all resources."""
    return_config: Dict[str, Any] = configs[0].copy()
    if len(configs) > 1:
        for config in configs[1:]:
            for key, value in config.items():
                if key in return_config and return_config[key] != value:
                    del return_config[key]
    num_empty_configs = 0
    for config in configs:
        keys_to_delete = []
        for key, value in config.items():
            if key in return_config:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            del config[key]
        if not config:
            num_empty_configs += 1

    if num_empty_configs == len(configs):
        return return_config
    if len(configs) > 0:
        return_config[group_key] = configs
    return return_config
