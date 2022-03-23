"""Task: a coarse-grained stage in an application."""
import inspect
import os
import re
from typing import Callable, Dict, List, Optional, Set, Union
from urllib import parse
import yaml

import sky
from sky import clouds
from sky import resources as resources_lib
from sky.data import storage as storage_lib

Resources = resources_lib.Resources
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


def is_cloud_store_url(url):
    result = parse.urlsplit(url)
    # '' means non-cloud URLs.
    return result.netloc


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


class Task:
    """Task: a coarse-grained stage in an application."""

    def __init__(
        self,
        name: Optional[str] = None,
        *,
        setup: Optional[str] = None,
        run: Optional[CommandOrCommandGen] = None,
        workdir: Optional[str] = None,
        num_nodes: Optional[int] = None,
        # Advanced:
        docker_image: Optional[str] = None,
    ):
        """Initializes a Task.

        All fields are optional.  `Task.run` is the actual program: either a
        shell command to run (str) or a command generator for different nodes
        (lambda; see below).

        Before executing a Task, it is required to call Task.set_resources() to
        assign resource requirements to this task.

        Args:
          name: A string name for the Task.
          setup: A setup command, run under 'workdir' and before actually
            executing the run command, 'run'.
          run: Either a shell command (str) or a command generator (callable).
            If latter, it must take a node rank and a list of node addresses as
            input and return a shell command (str) (valid to return None for
            some nodes, in which case no commands are run on them).  Commands
            will be run under 'workdir'. Note the command generator should be
            self-contained.
          workdir: The local working directory.  This directory and its files
            will be synced to a location on the remote VM(s), and 'setup' and
            'run' commands will be run under that location (thus, they can rely
            on relative paths when invoking binaries).
          num_nodes: The number of nodes to provision for this Task.  If None,
            treated as 1 node.  If > 1, each node will execute its own
            setup/run command; 'run' can either be a str, meaning all nodes get
            the same command, or a lambda, as documented above.
          docker_image: The base docker image that this Task will be built on.
            In effect when LocalDockerBackend is used.  Defaults to
            'gpuci/miniconda-cuda:11.4-runtime-ubuntu18.04'.
        """
        self.name = name
        self.run = run
        self.storage_mounts = {}
        self.storage_plans = {}
        self.setup = setup
        self.workdir = workdir
        self.docker_image = (docker_image if docker_image else
                             'gpuci/miniconda-cuda:11.4-runtime-ubuntu18.04')
        self.num_nodes = num_nodes
        self.inputs = None
        self.outputs = None
        self.estimated_inputs_size_gigabytes = None
        self.estimated_outputs_size_gigabytes = None
        # Default to CPUNode
        self.resources = {sky.Resources()}
        self.time_estimator_func = None
        self.file_mounts = None
        # Filled in by the optimizer.  If None, this Task is not planned.
        self.best_resources = None

        # Check if the task is legal.
        self._validate()

        dag = sky.DagContext.get_current_dag()
        dag.add(self)

    def _validate(self):
        """Checks if the Task fields are valid."""
        if not _is_valid_name(self.name):
            raise ValueError(f'Invalid task name {self.name}. Valid name: '
                             f'{_VALID_NAME_DESCR}')

        # Check self.run
        if callable(self.run):
            run_sig = inspect.signature(self.run)
            # Check that run is a function with 2 arguments.
            if len(run_sig.parameters) != 2:
                raise ValueError(_RUN_FN_CHECK_FAIL_MSG.format(run_sig))

            type_list = [int, List[str]]
            # Check annotations, if exists
            for i, param in enumerate(run_sig.parameters.values()):
                if param.annotation != inspect.Parameter.empty:
                    if param.annotation != type_list[i]:
                        raise ValueError(_RUN_FN_CHECK_FAIL_MSG.format(run_sig))

            # Check self containedness.
            run_closure = inspect.getclosurevars(self.run)
            if run_closure.nonlocals:
                raise ValueError(
                    'run command generator must be self contained. '
                    f'Found nonlocals: {run_closure.nonlocals}')
            if run_closure.globals:
                raise ValueError(
                    'run command generator must be self contained. '
                    f'Found globals: {run_closure.globals}')
            if run_closure.unbound:
                # Do not raise an error here. Import statements, which are
                # allowed, will be considered as unbounded.
                pass
        elif self.run is not None and not isinstance(self.run, str):
            raise ValueError('run must be either a shell script (str) or '
                             f'a command generator ({CommandGen}). '
                             f'Got {type(self.run)}')

        # Workdir.
        if self.workdir is not None:
            full_workdir = os.path.abspath(os.path.expanduser(self.workdir))
            if not os.path.isdir(full_workdir):
                # Symlink to a dir is legal (isdir() follows symlinks).
                raise ValueError(
                    'Workdir must exist and must be a directory (or '
                    f'a symlink to a directory). Found: {self.workdir}')

    @staticmethod
    def from_yaml(yaml_path):
        with open(os.path.expanduser(yaml_path), 'r') as f:
            config = yaml.safe_load(f)

        if isinstance(config, str):
            raise ValueError('YAML loaded as str, not as dict. '
                             f'Is it correct? Path: {yaml_path}')

        if config is None:
            config = {}

        # TODO: perform more checks on yaml and raise meaningful errors.
        task = Task(
            config.get('name'),
            run=config.get('run'),
            workdir=config.get('workdir'),
            setup=config.get('setup'),
            num_nodes=config.get('num_nodes'),
        )

        # Create lists to store storage objects inlined in file_mounts.
        # These are retained in dicts in the YAML schema and later parsed to
        # storage objects with the storage/storage_mount objects.
        fm_storages = []
        fm_storage_mounts = []
        file_mounts = config.get('file_mounts')
        if file_mounts is not None:
            copy_mounts = dict()
            for dst_path, src in file_mounts.items():
                # Check if it is str path
                if isinstance(src, str):
                    copy_mounts[dst_path] = src
                # If the src is not a str path, it is likely a dict. Try to
                # parse storage object.
                elif isinstance(src, dict):
                    name = src.get('name')
                    source = src.get('source')
                    if not name or not source:
                        raise ValueError('Inline storage objects need both name'
                                         ' and source path to be specified.')
                    fm_storages.append(src)
                    fm_storage_mounts.append({
                        'storage': name,
                        'mount_path': dst_path
                    })
                else:
                    raise ValueError(f'Unable to parse file_mount '
                                     f'{dst_path}:{src}')
            task.set_file_mounts(copy_mounts)

        # Process storage objects - both from file_mounts and from YAML
        yaml_storages = config.get('storage')
        if yaml_storages is not None:
            if isinstance(yaml_storages, dict):
                yaml_storages = [yaml_storages]
            if not isinstance(yaml_storages, list):
                raise ValueError(f'Invalid storage specification.'
                                 f' Expected list, got {yaml_storages}')
        else:
            yaml_storages = []

        task_storages = {}
        all_storages = yaml_storages + fm_storages
        for storage in all_storages:
            name = storage.get('name')
            source = storage.get('source')
            force_stores = storage.get('force_stores')
            assert name and source, \
                   'Storage Object needs name and source path specified.'
            persistent = True if storage.get(
                'persistent') is None else storage['persistent']
            storage_obj = storage_lib.Storage(name=name,
                                              source=source,
                                              persistent=persistent)
            if force_stores is not None:
                assert set(force_stores) <= {'s3', 'gcs', 'azure_blob'}
                for cloud_type in force_stores:
                    if cloud_type == 's3':
                        storage_obj.get_or_copy_to_s3()
                    elif cloud_type == 'gcs':
                        storage_obj.get_or_copy_to_gcs()
                    elif cloud_type == 'azure_blob':
                        storage_obj.get_or_copy_to_azure_blob()
            task_storages[name] = storage_obj

        # Process storage mount objects - both from file_mounts and from YAML
        yaml_storage_mounts = config.get('storage_mounts')
        if yaml_storage_mounts is not None:
            if isinstance(yaml_storage_mounts, dict):
                yaml_storage_mounts = [yaml_storage_mounts]
            if not isinstance(yaml_storage_mounts, list):
                raise ValueError(f'Invalid storage mount specification.'
                                 f' Expected list, got {yaml_storage_mounts}')
        else:
            yaml_storage_mounts = []

        all_storage_mounts = yaml_storage_mounts + fm_storage_mounts
        task_storage_mounts = {}
        for storage_mount in all_storage_mounts:
            name = storage_mount.get('storage')
            storage_mount_path = storage_mount.get('mount_path')
            assert name, \
                'Storage mount must have name reference to Storage object.'
            assert storage_mount_path, \
                'Storage mount path cannot be empty.'
            storage = task_storages[name]
            task_storage_mounts[storage_mount_path] = storage
        task.set_storage_mounts(task_storage_mounts)

        if config.get('inputs') is not None:
            inputs_dict = config['inputs']
            inputs = list(inputs_dict.keys())[0]
            estimated_size_gigabytes = list(inputs_dict.values())[0]
            # TODO: allow option to say (or detect) no download/egress cost.
            task.set_inputs(inputs=inputs,
                            estimated_size_gigabytes=estimated_size_gigabytes)

        if config.get('outputs') is not None:
            outputs_dict = config['outputs']
            outputs = list(outputs_dict.keys())[0]
            estimated_size_gigabytes = list(outputs_dict.values())[0]
            task.set_outputs(outputs=outputs,
                             estimated_size_gigabytes=estimated_size_gigabytes)

        resources = config.get('resources')
        if resources is not None:
            if resources.get('cloud') is not None:
                resources['cloud'] = clouds.CLOUD_REGISTRY[resources['cloud']]
            if resources.get('accelerators') is not None:
                resources['accelerators'] = resources['accelerators']
            if resources.get('accelerator_args') is not None:
                resources['accelerator_args'] = dict(
                    resources['accelerator_args'])
            if resources.get('use_spot') is not None:
                resources['use_spot'] = resources['use_spot']
            # FIXME: We should explicitly declare all the parameters
            # that are sliding through the **resources
            resources = sky.Resources(**resources)
        else:
            resources = sky.Resources()
        task.set_resources({resources})
        return task

    @property
    def num_nodes(self) -> int:
        return self._num_nodes

    @num_nodes.setter
    def num_nodes(self, num_nodes: Optional[int]) -> None:
        if num_nodes is None:
            num_nodes = 1
        if not isinstance(num_nodes, int) or num_nodes <= 0:
            raise ValueError(
                f'num_nodes should be a positive int. Got: {num_nodes}')
        self._num_nodes = num_nodes

    # E.g., 's3://bucket', 'gs://bucket', or None.
    def set_inputs(self, inputs, estimated_size_gigabytes):
        self.inputs = inputs
        self.estimated_inputs_size_gigabytes = estimated_size_gigabytes
        return self

    def get_inputs(self):
        return self.inputs

    def get_estimated_inputs_size_gigabytes(self):
        return self.estimated_inputs_size_gigabytes

    def get_inputs_cloud(self):
        """Returns the cloud my inputs live in."""
        assert isinstance(self.inputs, str), self.inputs
        if self.inputs.startswith('s3:'):
            return clouds.AWS()
        elif self.inputs.startswith('gs:'):
            return clouds.GCP()
        else:
            raise ValueError(f'cloud path not supported: {self.inputs}')

    def set_outputs(self, outputs, estimated_size_gigabytes):
        self.outputs = outputs
        self.estimated_outputs_size_gigabytes = estimated_size_gigabytes
        return self

    def get_outputs(self):
        return self.outputs

    def get_estimated_outputs_size_gigabytes(self):
        return self.estimated_outputs_size_gigabytes

    def set_resources(self, resources: Union[Resources, Set[Resources]]):
        """Sets the required resources to execute this task.

        Args:
          resources: either a sky.Resources, or a set of them.  The latter case
            indicates the user intent "pick any one of these resources" to run
            a task.
        """
        if isinstance(resources, Resources):
            resources = {resources}
        self.resources = resources
        return self

    def get_resources(self):
        return self.resources

    def set_time_estimator(self, func):
        """Sets a func mapping resources to estimated time (secs)."""
        self.time_estimator_func = func
        return self

    def estimate_runtime(self, resources):
        """Returns a func mapping resources to estimated time (secs)."""
        if self.time_estimator_func is None:
            raise NotImplementedError(
                'Node [{}] does not have a cost model set; '
                'call set_time_estimator() first'.format(self))
        return self.time_estimator_func(resources)

    def set_storage_mounts(
        self,
        storage_mounts: Dict[str, storage_lib.Storage],
    ):
        """Sets the storage mounts for this Task

        Advanced method for users. Storage mounts map a mount path on the Cloud
        VM to a Storage object (see sky/data/storage.py). The storage object
        can be from a local folder or from an existing cloud bucket.

        Example:
            task.set_storage_mounts({
                '/tmp/imagenet/': \
                Storage(name='imagenet', source='s3://imagenet-bucket'):
            })

        Args:
            storage_mounts: a dict of {mount_path: Storage}, where mount_path
            is the path on the Cloud VM where the Storage object will be
            mounted on
        """
        self.storage_mounts = storage_mounts
        return self

    def add_storage_mounts(self) -> None:
        """Adds storage mounts to the Task."""
        # Hack: Hardcode storage_plans to AWS for optimal plan
        # Optimizer is supposed to choose storage plan but we
        # move this here temporarily
        for store in self.storage_mounts.values():
            if len(store.stores) == 0:
                self.storage_plans[store] = storage_lib.StorageType.S3
                store.get_or_copy_to_s3()
            else:
                # Sky will download the first store that is added to remote
                self.storage_plans[store] = list(store.stores.keys())[0]

        storage_mounts = self.storage_mounts
        storage_plans = self.storage_plans
        for mnt_path, store in storage_mounts.items():
            storage_type = storage_plans[store]
            if storage_type is storage_lib.StorageType.S3:
                # TODO: allow for Storage mounting of different clouds
                self.update_file_mounts({
                    mnt_path: 's3://' + store.name,
                })
            elif storage_type is storage_lib.StorageType.GCS:
                # Remember to run `gcloud auth application-default login`
                self.setup = (
                    '([[ -z $GOOGLE_APPLICATION_CREDENTIALS ]] && '
                    'echo GOOGLE_APPLICATION_CREDENTIALS='
                    '~/.config/gcloud/application_default_credentials.json >> '
                    f'~/.bashrc || true); {self.setup or "true"}')
                self.update_file_mounts({
                    mnt_path: 'gs://' + store.name,
                })
            elif storage_type is storage_lib.StorageType.AZURE:
                # TODO when Azure Blob is done: sync ~/.azure
                assert False, 'TODO: Azure Blob not mountable yet'
            else:
                raise ValueError(f'Storage Type {storage_type} \
                    does not exist!')

    def set_file_mounts(self, file_mounts: Optional[Dict[str, str]]) -> None:
        """Sets the file mounts for this Task.

        File mounts are a dictionary of { remote_path: local_path/cloud URI }.
        Local (or cloud) files/directories will be synced to the specified
        paths on the remote VM(s) where this Task will run.

        Used for syncing datasets, dotfiles, etc.

        Paths cannot end with a slash (for clarity).

        Example:

            task.set_file_mounts({
                '~/.dotfile': '/local/.dotfile',
                # /remote/dir/ will contain the contents of /local/dir/.
                '/remote/dir': '/local/dir',
            })

        Args:
          file_mounts: either None or a dict of { remote_path: local_path/cloud
            URI }, where remote is the VM on which this Task will eventually
            run on, and local is the node from which the task is launched.
        """
        if file_mounts is None:
            self.file_mounts = None
            return self
        for target, source in file_mounts.items():
            if target.endswith('/') or source.endswith('/'):
                raise ValueError(
                    'File mount paths cannot end with a slash '
                    '(try "/mydir: /mydir" or "/myfile: /myfile"). '
                    f'Found: target={target} source={source}')
            if is_cloud_store_url(target):
                raise ValueError(
                    'File mount destination paths cannot be cloud storage')
            if not is_cloud_store_url(source):
                if not os.path.exists(
                        os.path.abspath(os.path.expanduser(source))):
                    raise ValueError(
                        f'File mount source {source!r} does not exist locally. '
                        'To fix: check if it exists, and correct the path.')

        self.file_mounts = file_mounts
        return self

    def update_file_mounts(self, file_mounts: Dict[str, str]):
        """Updates the file mounts for this Task.

        This should be run before provisioning.

        Example:

            task.update_file_mounts({
                '~/.config': '~/Documents/config',
                '/tmp/workdir': '/local/workdir/cnn-cifar10',
            })

        Args:
          file_mounts: a dict of { remote_path: local_path }, where remote is
            the VM on which this Task will eventually run on, and local is the
            node from which the task is launched.
        """
        if self.file_mounts is None:
            self.file_mounts = {}
        self.file_mounts.update(file_mounts)
        # For validation logic:
        return self.set_file_mounts(self.file_mounts)

    def get_local_to_remote_file_mounts(self) -> Optional[Dict[str, str]]:
        """Returns file mounts of the form (dst=VM path, src=local path).

        Any cloud object store URLs (gs://, s3://, etc.), either as source or
        destination, are not included.
        """
        if self.file_mounts is None:
            return None
        d = {}
        for k, v in self.file_mounts.items():
            if not is_cloud_store_url(k) and not is_cloud_store_url(v):
                d[k] = v
        return d

    def get_cloud_to_remote_file_mounts(self) -> Optional[Dict[str, str]]:
        """Returns file mounts of the form (dst=VM path, src=cloud URL).

        Local-to-remote file mounts are excluded (handled by
        get_local_to_remote_file_mounts()).
        """
        if self.file_mounts is None:
            return None
        d = {}
        for k, v in self.file_mounts.items():
            if not is_cloud_store_url(k) and is_cloud_store_url(v):
                d[k] = v
        return d

    def __rshift__(self, b):
        sky.DagContext.get_current_dag().add_edge(self, b)

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
