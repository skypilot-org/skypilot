"""Task: a coarse-grained stage in an application."""
import inspect
import os
import re
import typing
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import yaml

import sky
from sky import check
from sky import clouds
from sky import exceptions
from sky import global_user_state
from sky.backends import backend_utils
from sky.data import storage as storage_lib
from sky.data import data_utils
from sky.skylet import constants
from sky.utils import schemas
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib

# A lambda generating commands (node rank_i, node addrs -> cmd_i).
CommandGen = Callable[[int, List[str]], Optional[str]]
CommandOrCommandGen = Union[str, CommandGen]

_VALID_NAME_REGEX = '[a-z0-9]+(?:[._-]{1,2}[a-z0-9]+)*'
_VALID_ENV_VAR_REGEX = '[a-zA-Z_][a-zA-Z0-9_]*'
_VALID_NAME_DESCR = ('ASCII characters and may contain lowercase and'
                     ' uppercase letters, digits, underscores, periods,'
                     ' and dashes. Must start and end with alphanumeric'
                     ' characters. No triple dashes or underscores.')

_RUN_FN_CHECK_FAIL_MSG = (
    'run command generator must take exactly 2 arguments: node_rank (int) and'
    'a list of node ip addresses (List[str]). Got {run_sig}')


def _is_valid_name(name: str) -> bool:
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


def _is_valid_env_var(name: str) -> bool:
    """Checks if the task environment variable name is valid."""
    return bool(re.fullmatch(_VALID_ENV_VAR_REGEX, name))


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
    ):
        """Initializes a Task.

        All fields are optional.  ``Task.run`` is the actual program: either a
        shell command to run (str) or a command generator for different nodes
        (lambda; see below).

        Optionally, call ``Task.set_resources()`` to set the resource
        requirements for this task.  If not set, a default CPU-only requirement
        is assumed (the same as ``sky cpunode``).

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
        """
        self.name = name
        self.run = run
        self.storage_mounts = {}
        self.storage_plans = {}
        self.setup = setup
        self._envs = envs or dict()
        self.workdir = workdir
        self.docker_image = (docker_image if docker_image else
                             'gpuci/miniforge-cuda:11.4-devel-ubuntu18.04')
        self.num_nodes = num_nodes

        self.inputs = None
        self.outputs = None
        self.estimated_inputs_size_gigabytes = None
        self.estimated_outputs_size_gigabytes = None
        # Default to CPUNode
        self.resources = {sky.Resources()}
        self.time_estimator_func = None
        self.file_mounts = None

        # Only set when 'self' is a spot controller task: 'self.spot_task' is
        # the underlying managed spot task (Task object).
        self.spot_task = None

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
        with open(os.path.expanduser(yaml_path), 'r') as f:
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

        backend_utils.validate_schema(config, schemas.get_task_schema(),
                                      'Invalid task YAML: ')

        task = Task(
            config.pop('name', None),
            run=config.pop('run', None),
            workdir=config.pop('workdir', None),
            setup=config.pop('setup', None),
            num_nodes=config.pop('num_nodes', None),
            envs=config.pop('envs', None),
        )

        # Create lists to store storage objects inlined in file_mounts.
        # These are retained in dicts in the YAML schema and later parsed to
        # storage objects with the storage/storage_mount objects.
        fm_storages = []
        file_mounts = config.pop('file_mounts', None)
        if file_mounts is not None:
            copy_mounts = dict()
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

        task_storage_mounts = {}  # type: Dict[str, Storage]
        all_storages = fm_storages
        for storage in all_storages:
            mount_path = storage[0]
            assert mount_path, \
                'Storage mount path cannot be empty.'
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

        resources = config.pop('resources', None)
        resources = sky.Resources.from_yaml_config(resources)

        task.set_resources({resources})
        assert not config, f'Invalid task args: {config.keys()}'
        return task

    @property
    def num_nodes(self) -> int:
        return self._num_nodes

    @property
    def envs(self) -> Dict[str, str]:
        return self._envs

    def set_envs(
            self, envs: Union[None, Tuple[Tuple[str, str]],
                              Dict[str, str]]) -> 'Task':
        """Sets the environment variables for use inside the setup/run commands.

        Args:
          envs: (optional) either a list of ``(env_name, value)`` or a dict
            ``{env_name: value}``.

        Returns:
          self: The current task, with envs set.

        Raises:
          ValueError: if various invalid inputs errors are detected.
        """
        if envs is None:
            self._envs = dict()
            return self
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
                if not _is_valid_env_var(key):
                    with ux_utils.print_exception_no_traceback():
                        raise ValueError(f'Invalid env key: {key}')
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'envs must be List[Tuple[str, str]] or Dict[str, str]: '
                    f'{envs}')
        self._envs = envs
        return self

    @property
    def need_spot_recovery(self) -> bool:
        return any(r.spot_recovery is not None for r in self.resources)

    @num_nodes.setter
    def num_nodes(self, num_nodes: Optional[int]) -> None:
        if num_nodes is None:
            num_nodes = 1
        if not isinstance(num_nodes, int) or num_nodes <= 0:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'num_nodes should be a positive int. Got: {num_nodes}')
        self._num_nodes = num_nodes

    def set_inputs(self, inputs, estimated_size_gigabytes) -> 'Task':
        # E.g., 's3://bucket', 'gs://bucket', or None.
        self.inputs = inputs
        self.estimated_inputs_size_gigabytes = estimated_size_gigabytes
        return self

    def get_inputs(self):
        return self.inputs

    def get_estimated_inputs_size_gigabytes(self):
        return self.estimated_inputs_size_gigabytes

    def get_inputs_cloud(self):
        """EXPERIMENTAL: Returns the cloud my inputs live in."""
        assert isinstance(self.inputs, str), self.inputs
        if self.inputs.startswith('s3:'):
            return clouds.AWS()
        elif self.inputs.startswith('gs:'):
            return clouds.GCP()
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'cloud path not supported: {self.inputs}')

    def set_outputs(self, outputs, estimated_size_gigabytes) -> 'Task':
        self.outputs = outputs
        self.estimated_outputs_size_gigabytes = estimated_size_gigabytes
        return self

    def get_outputs(self):
        return self.outputs

    def get_estimated_outputs_size_gigabytes(self):
        return self.estimated_outputs_size_gigabytes

    def set_resources(
        self, resources: Union['resources_lib.Resources',
                               Set['resources_lib.Resources']]
    ) -> 'Task':
        """Sets the required resources to execute this task.

        If this function is not called for a Task, default resource
        requirements will be used (8 vCPUs).

        Args:
          resources: either a sky.Resources, or a set of them.  The latter case
            is EXPERIMENTAL and indicates asking the optimizer to "pick the
            best of these resources" to run this task.

        Returns:
          self: The current task, with resources set.
        """
        if isinstance(resources, sky.Resources):
            resources = {resources}
        self.resources = resources
        return self

    def get_resources(self):
        return self.resources

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
                'Node [{}] does not have a cost model set; '
                'call set_time_estimator() first'.format(self))
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
                if not os.path.exists(
                        os.path.abspath(os.path.expanduser(source))):
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
            self.storage_mounts = None
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
        overwritting it.

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
        #  3. the first enabled cloud.
        # This should be refactored and moved to the optimizer.
        assert len(self.resources) == 1, self.resources
        storage_cloud = None
        if self.best_resources is not None:
            storage_cloud = self.best_resources.cloud
        else:
            resources = list(self.resources)[0]
            storage_cloud = resources.cloud
            if storage_cloud is None:
                # Get the first enabled cloud.
                enabled_clouds = global_user_state.get_enabled_clouds()
                if len(enabled_clouds) == 0:
                    check.check(quiet=True)
                    enabled_clouds = global_user_state.get_enabled_clouds()
                if len(enabled_clouds) == 0:
                    raise ValueError('No enabled clouds.')

                for cloud in storage_lib.STORE_ENABLED_CLOUDS:
                    for enabled_cloud in enabled_clouds:
                        if cloud.is_same_cloud(enabled_cloud):
                            storage_cloud = cloud
                            break
        if storage_cloud is None:
            raise ValueError('No available cloud to mount storage.')
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
                    if storage.source is not None and storage.source.startswith(
                            's3://'):
                        blob_path = storage.source
                    else:
                        blob_path = 's3://' + storage.name
                    self.update_file_mounts({
                        mnt_path: blob_path,
                    })
                elif store_type is storage_lib.StoreType.GCS:
                    if storage.source.startswith('gs://'):
                        blob_path = storage.source
                    else:
                        blob_path = 'gs://' + storage.name
                    self.update_file_mounts({
                        mnt_path: blob_path,
                    })
                elif store_type is storage_lib.StoreType.AZURE:
                    # TODO when Azure Blob is done: sync ~/.azure
                    assert False, 'TODO: Azure Blob not mountable yet'
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
        config = dict()

        def add_if_not_none(key, value, no_empty: bool = False):
            if no_empty and not value:
                return
            if value is not None:
                config[key] = value

        add_if_not_none('name', self.name)

        if self.resources is not None:
            assert len(self.resources) == 1
            resources = list(self.resources)[0]
            add_if_not_none('resources', resources.to_yaml_config())
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
        add_if_not_none('run', self.run)
        add_if_not_none('envs', self.envs, no_empty=True)

        add_if_not_none('file_mounts', dict())

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
        if self.name:
            return self.name
        if isinstance(self.run, str):
            run_msg = self.run.replace('\n', '\\n')
            if len(run_msg) > 20:
                run_msg = f'run=\'{run_msg[:20]}...\''
            else:
                run_msg = f'run=\'{run_msg}\''
        elif self.run is None:
            run_msg = 'run=None'
        else:
            run_msg = 'run=<fn>'

        s = f'Task({run_msg})'
        if self.inputs is not None:
            s += f'\n  inputs: {self.inputs}'
        if self.outputs is not None:
            s += f'\n  outputs: {self.outputs}'
        if self.num_nodes > 1:
            s += f'\n  nodes: {self.num_nodes}'
        if len(self.resources) > 1 or not list(self.resources)[0].is_empty():
            s += f'\n  resources: {self.resources}'
        else:
            s += '\n  resources: default instances'
        return s
