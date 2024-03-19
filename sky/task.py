"""Task: a coarse-grained stage in an application."""
import inspect
import json
import os
import re
import typing
from typing import (Any, Callable, Dict, Iterable, List, Optional, Set, Tuple,
                    Union)

import colorama
import yaml

import sky
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
import sky.dag
from sky.data import data_utils
from sky.data import storage as storage_lib
from sky.provision import docker_utils
from sky.serve import service_spec
from sky.skylet import constants
from sky.utils import common_utils
from sky.utils import schemas
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

logger = sky_logging.init_logger(__name__)

# A lambda generating commands (node rank_i, node addrs -> cmd_i).
CommandGen = Callable[[int, List[str]], Optional[str]]
CommandOrCommandGen = Union[str, CommandGen]

_VALID_NAME_REGEX = '[a-z0-9]+(?:[._-]{1,2}[a-z0-9]+)*'
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


def _check_docker_login_config(task_envs: Dict[str, str]) -> bool:
    """Checks if there is a valid docker login config in task_envs.

    If any of the docker login env vars is set, all of them must be set.

    Raises:
        ValueError: if any of the docker login env vars is set, but not all of
            them are set.
    """
    all_keys = constants.DOCKER_LOGIN_ENV_VARS
    existing_keys = all_keys & set(task_envs.keys())
    if not existing_keys:
        return False
    if len(existing_keys) != len(all_keys):
        with ux_utils.print_exception_no_traceback():
            raise ValueError(
                f'If any of {", ".join(all_keys)} is set, all of them must '
                f'be set. Missing envs: {all_keys - existing_keys}')
    return True


def _with_docker_login_config(
    resources: Union[Set['resources_lib.Resources'],
                     List['resources_lib.Resources']],
    task_envs: Dict[str, str],
) -> Union[Set['resources_lib.Resources'], List['resources_lib.Resources']]:
    if not _check_docker_login_config(task_envs):
        return resources
    docker_login_config = docker_utils.DockerLoginConfig.from_env_vars(
        task_envs)

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
        assert len(resources.image_id) == 1, resources.image_id
        region = list(resources.image_id.keys())[0]
        return resources.copy(image_id={region: 'docker:' + docker_image},
                              _docker_login_config=docker_login_config)

    new_resources = []
    for r in resources:
        new_resources.append(_add_docker_login_config(r))
    return type(resources)(new_resources)


class Task:
    """Task: a computation to be run on the cloud."""

    def __init__(
        self,
        name: Optional[str] = None,
        *,
        setup: Optional[str] = None,
        run: Optional[CommandOrCommandGen] = None,
        envs: Optional[Dict[str, str]] = None,
        workdir: Optional[str] = None,
        num_nodes: Optional[int] = None,
        # Advanced:
        docker_image: Optional[str] = None,
        event_callback: Optional[str] = None,
        blocked_resources: Optional[Iterable['resources_lib.Resources']] = None,
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
          setup: A setup command, which will be run before executing the run
            commands ``run``, and executed under ``workdir``.
          run: The actual command for the task. If not None, either a shell
            command (str) or a command generator (callable).  If latter, it
            must take a node rank and a list of node addresses as input and
            return a shell command (str) (valid to return None for some nodes,
            in which case no commands are run on them).  Run commands will be
            run under ``workdir``. Note the command generator should be a
            self-contained lambda.
          envs: A dictionary of environment variables to set before running the
            setup and run commands.
          workdir: The local working directory.  This directory will be synced
            to a location on the remote VM(s), and ``setup`` and ``run``
            commands will be run under that location (thus, they can rely on
            relative paths when invoking binaries).
          num_nodes: The number of nodes to provision for this Task.  If None,
            treated as 1 node.  If > 1, each node will execute its own
            setup/run command, where ``run`` can either be a str, meaning all
            nodes get the same command, or a lambda, with the semantics
            documented above.
          docker_image: (EXPERIMENTAL: Only in effect when LocalDockerBackend
            is used.) The base docker image that this Task will be built on.
            Defaults to 'gpuci/miniforge-cuda:11.4-devel-ubuntu18.04'.
          blocked_resources: A set of resources that this task cannot run on.
        """
        self.name = name
        self.run = run
        self.storage_mounts: Dict[str, storage_lib.Storage] = {}
        self.storage_plans: Dict[storage_lib.Storage,
                                 storage_lib.StoreType] = {}
        self.setup = setup
        self._envs = envs or {}
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
        self.resources: Union[List[sky.Resources],
                              Set[sky.Resources]] = {sky.Resources()}
        self._service: Optional[service_spec.SkyServiceSpec] = None
        # Resources that this task cannot run on.
        self.blocked_resources = blocked_resources

        self.time_estimator_func: Optional[Callable[['sky.Resources'],
                                                    int]] = None
        self.file_mounts: Optional[Dict[str, str]] = None

        # Only set when 'self' is a spot controller task: 'self.spot_dag' is
        # the underlying managed spot dag (sky.Dag object).
        self.spot_dag: Optional['sky.Dag'] = None

        # Only set when 'self' is a sky serve controller task.
        self.service_name: Optional[str] = None

        # Filled in by the optimizer.  If None, this Task is not planned.
        self.best_resources = None
        # Check if the task is legal.
        self._validate()

        dag = sky.dag.get_current_dag()
        if dag is not None:
            dag.add(self)

    def _validate(self):
        """Checks if the Task fields are valid."""
        if not _is_valid_name(self.name):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Invalid task name {self.name}. Valid name: '
                                 f'{_VALID_NAME_DESCR}')

        # Check self.run
        if callable(self.run):
            run_sig = inspect.signature(self.run)
            # Check that run is a function with 2 arguments.
            if len(run_sig.parameters) != 2:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(_RUN_FN_CHECK_FAIL_MSG.format(run_sig))

            type_list = [int, List[str]]
            # Check annotations, if exists
            for i, param in enumerate(run_sig.parameters.values()):
                if param.annotation != inspect.Parameter.empty:
                    if param.annotation != type_list[i]:
                        with ux_utils.print_exception_no_traceback():
                            raise ValueError(
                                _RUN_FN_CHECK_FAIL_MSG.format(run_sig))

            # Check self containedness.
            run_closure = inspect.getclosurevars(self.run)
            if run_closure.nonlocals:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'run command generator must be self contained. '
                        f'Found nonlocals: {run_closure.nonlocals}')
            if run_closure.globals:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'run command generator must be self contained. '
                        f'Found globals: {run_closure.globals}')
            if run_closure.unbound:
                # Do not raise an error here. Import statements, which are
                # allowed, will be considered as unbounded.
                pass
        elif self.run is not None and not isinstance(self.run, str):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('run must be either a shell script (str) or '
                                 f'a command generator ({CommandGen}). '
                                 f'Got {type(self.run)}')

        # Workdir.
        if self.workdir is not None:
            full_workdir = os.path.abspath(os.path.expanduser(self.workdir))
            if not os.path.isdir(full_workdir):
                # Symlink to a dir is legal (isdir() follows symlinks).
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Workdir must exist and must be a directory (or '
                        f'a symlink to a directory). {self.workdir} not found.')

    @staticmethod
    def from_yaml_config(
        config: Dict[str, Any],
        env_overrides: Optional[List[Tuple[str, str]]] = None,
    ) -> 'Task':
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

        # More robust handling for 'envs': explicitly convert keys and values to
        # str, since users may pass '123' as keys/values which will get parsed
        # as int causing validate_schema() to fail.
        envs = config.get('envs')
        if envs is not None and isinstance(envs, dict):
            config['envs'] = {str(k): str(v) for k, v in envs.items()}

        common_utils.validate_schema(config, schemas.get_task_schema(),
                                     'Invalid task YAML: ')

        # Fill in any Task.envs into file_mounts (src/dst paths, storage
        # name/source).
        if config.get('file_mounts') is not None:
            config['file_mounts'] = _fill_in_env_vars(config['file_mounts'],
                                                      config.get('envs', {}))

        # Fill in any Task.envs into service (e.g. MODEL_NAME).
        if config.get('service') is not None:
            config['service'] = _fill_in_env_vars(config['service'],
                                                  config.get('envs', {}))

        task = Task(
            config.pop('name', None),
            run=config.pop('run', None),
            workdir=config.pop('workdir', None),
            setup=config.pop('setup', None),
            num_nodes=config.pop('num_nodes', None),
            envs=config.pop('envs', None),
            event_callback=config.pop('event_callback', None),
        )

        # Create lists to store storage objects inlined in file_mounts.
        # These are retained in dicts in the YAML schema and later parsed to
        # storage objects with the storage/storage_mount objects.
        fm_storages = []
        file_mounts = config.pop('file_mounts', None)
        if file_mounts is not None:
            copy_mounts = {}
            for dst_path, src in file_mounts.items():
                # Check if it is str path
                if isinstance(src, str):
                    copy_mounts[dst_path] = src
                # If the src is not a str path, it is likely a dict. Try to
                # parse storage object.
                elif isinstance(src, dict):
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

        # Parse resources field.
        resources_config = config.pop('resources', None)
        task.set_resources(sky.Resources.from_yaml_config(resources_config))

        service = config.pop('service', None)
        if service is not None:
            service = service_spec.SkyServiceSpec.from_yaml_config(service)
        task.set_service(service)

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
            config = yaml.safe_load(f)

        if isinstance(config, str):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('YAML loaded as str, not as dict. '
                                 f'Is it correct? Path: {yaml_path}')

        if config is None:
            config = {}
        return Task.from_yaml_config(config)

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
    def envs(self) -> Dict[str, str]:
        return self._envs

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
        if _check_docker_login_config(self._envs):
            self.resources = _with_docker_login_config(self.resources,
                                                       self._envs)
        return self

    @property
    def need_spot_recovery(self) -> bool:
        return any(r.spot_recovery is not None for r in self.resources)

    @property
    def use_spot(self) -> bool:
        return any(r.use_spot for r in self.resources)

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

    def set_resources(
        self, resources: Union['resources_lib.Resources',
                               List['resources_lib.Resources'],
                               Set['resources_lib.Resources']]
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
        if isinstance(resources, sky.Resources):
            resources = {resources}
        # TODO(woosuk): Check if the resources are None.
        self.resources = _with_docker_login_config(resources, self.envs)
        return self

    def set_resources_override(self, override_params: Dict[str, Any]) -> 'Task':
        """Sets the override parameters for the resources."""
        new_resources_list = []
        for res in list(self.resources):
            new_resources = res.copy(**override_params)
            new_resources_list.append(new_resources)

        self.set_resources(type(self.resources)(new_resources_list))
        return self

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

    def set_time_estimator(self, func: Callable[['sky.Resources'],
                                                int]) -> 'Task':
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

        Raises:
          ValueError: if input paths are invalid.
        """
        if file_mounts is None:
            self.file_mounts = None
            return self
        for target, source in file_mounts.items():
            if target.endswith('/') or source.endswith('/'):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'File mount paths cannot end with a slash '
                        '(try "/mydir: /mydir" or "/myfile: /myfile"). '
                        f'Found: target={target} source={source}')
            if data_utils.is_cloud_store_url(target):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'File mount destination paths cannot be cloud storage')
            if not data_utils.is_cloud_store_url(source):
                if (not os.path.exists(
                        os.path.abspath(os.path.expanduser(source))) and
                        not source.startswith('skypilot:')):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(
                            f'File mount source {source!r} does not exist '
                            'locally. To fix: check if it exists, and correct '
                            'the path.')
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

        self.file_mounts = file_mounts
        return self

    def update_file_mounts(self, file_mounts: Dict[str, str]) -> 'Task':
        """Updates the file mounts for this task.

        Different from set_file_mounts(), this function updates into the
        existing file_mounts (calls ``dict.update()``), rather than
        overwritting it.

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
        # For validation logic:
        return self.set_file_mounts(self.file_mounts)

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
            return self
        for target, _ in storage_mounts.items():
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

    def get_preferred_store_type(self) -> storage_lib.StoreType:
        # TODO(zhwu, romilb): The optimizer should look at the source and
        #  destination to figure out the right stores to use. For now, we
        #  use a heuristic solution to find the store type by the following
        #  order:
        #  1. cloud decided in best_resources.
        #  2. cloud specified in the task resources.
        #  3. if not specified or the task's cloud does not support storage,
        #     use the first enabled storage cloud.
        # This should be refactored and moved to the optimizer.

        # This check is not needed to support multiple accelerators;
        # We just need to get the storage_cloud.
        # assert len(self.resources) == 1, self.resources
        storage_cloud = None

        backend_utils.check_public_cloud_enabled()
        enabled_storage_clouds = global_user_state.get_enabled_storage_clouds()
        if not enabled_storage_clouds:
            raise ValueError('No enabled cloud for storage, run: sky check')

        if self.best_resources is not None:
            storage_cloud = self.best_resources.cloud
        else:
            resources = list(self.resources)[0]
            storage_cloud = resources.cloud
        if storage_cloud is not None:
            if str(storage_cloud) not in enabled_storage_clouds:
                storage_cloud = None

        if storage_cloud is None:
            storage_cloud = clouds.CLOUD_REGISTRY.from_str(
                enabled_storage_clouds[0])
            assert storage_cloud is not None, enabled_storage_clouds[0]

        store_type = storage_lib.get_storetype_from_cloud(storage_cloud)
        return store_type

    def sync_storage_mounts(self) -> None:
        """(INTERNAL) Eagerly syncs storage mounts to cloud storage.

        After syncing up, COPY-mode storage mounts are translated into regular
        file_mounts of the form ``{ /remote/path: {s3,gs,..}://<bucket path>
        }``.
        """
        for storage in self.storage_mounts.values():
            if len(storage.stores) == 0:
                store_type = self.get_preferred_store_type()
                self.storage_plans[storage] = store_type
                storage.add_store(store_type)
            else:
                # We will download the first store that is added to remote.
                self.storage_plans[storage] = list(storage.stores.keys())[0]

        storage_mounts = self.storage_mounts
        storage_plans = self.storage_plans
        for mnt_path, storage in storage_mounts.items():
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
                            storage.name, data_utils.Rclone.RcloneClouds.IBM)
                        blob_path = f'cos://{cos_region}/{storage.name}'
                    self.update_file_mounts({mnt_path: blob_path})
                elif store_type is storage_lib.StoreType.AZURE:
                    # TODO when Azure Blob is done: sync ~/.azure
                    raise NotImplementedError('Azure Blob not mountable yet')
                else:
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(f'Storage Type {store_type} '
                                         'does not exist!')

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
        """Returns whether this task is a spot/serve controller process."""
        return self.spot_dag is not None or self.service_name is not None

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

    def to_yaml_config(self) -> Dict[str, Any]:
        """Returns a yaml-style dict representation of the task.

        INTERNAL: this method is internal-facing.
        """
        config = {}

        def add_if_not_none(key, value, no_empty: bool = False):
            if no_empty and not value:
                return
            if value is not None:
                config[key] = value

        add_if_not_none('name', self.name)

        tmp_resource_config = {}
        if len(self.resources) > 1:
            resource_list = []
            for r in self.resources:
                resource_list.append(r.to_yaml_config())
            key = 'ordered' if isinstance(self.resources, list) else 'any_of'
            tmp_resource_config[key] = resource_list
        else:
            tmp_resource_config = list(self.resources)[0].to_yaml_config()

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
        add_if_not_none('envs', self.envs, no_empty=True)

        add_if_not_none('file_mounts', {})

        if self.file_mounts is not None:
            config['file_mounts'].update(self.file_mounts)

        if self.storage_mounts is not None:
            config['file_mounts'].update({
                mount_path: storage.to_yaml_config()
                for mount_path, storage in self.storage_mounts.items()
            })
        return config

    def __rshift__(self, b):
        sky.dag.get_current_dag().add_edge(self, b)

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
