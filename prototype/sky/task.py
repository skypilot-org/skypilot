"""Task: a coarse-grained stage in an application."""
import os
from typing import Callable, Dict, List, Optional, Set, Union
from urllib import parse
import yaml

import sky
from sky import clouds
from sky import resources as resources_lib
from sky.data import storage as storage_lib

Resources = resources_lib.Resources
# A lambda generating commands (node addrs -> {addr: cmd_i}).
CommandGen = Callable[[List[str]], Dict[str, str]]
CommandOrCommandGen = Union[str, CommandGen]

CLOUD_REGISTRY = {
    'aws': clouds.AWS(),
    'gcp': clouds.GCP(),
    'azure': clouds.Azure(),
}


def _is_cloud_store_url(url):
    result = parse.urlsplit(url)
    # '' means non-cloud URLs.
    return result.netloc


class Task(object):
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
            post_setup_fn: Optional[CommandGen] = None,
            docker_image: Optional[str] = None,
            container_name: Optional[str] = None,
            private_key: Optional[str] = '~/.ssh/sky-key',
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
            If latter, it must take a list of node addresses as input and
            return a dictionary {addr: command for addr} (valid to exclude some
            nodes, in which case no commands are run on them).  Commands will
            be run under 'workdir'.
          workdir: The local working directory.  This directory and its files
            will be synced to a location on the remote VM(s), and 'setup' and
            'run' commands will be run under that location (thus, they can rely
            on relative paths when invoking binaries).
          num_nodes: The number of nodes to provision for this Task.  If None,
            treated as 1 node.  If > 1, each node will execute its own
            setup/run command; 'run' can either be a str, meaning all nodes get
            the same command, or a lambda, as documented above.
          post_setup_fn: If specified, this generates commands to be run on all
            node(s), which are run after resource provisioning and 'setup' but
            before 'run'.  A typical use case is to set environment variables
            on each node based on all node IPs.
          docker_image: The base docker image that this Task will be built on.
            In effect when LocalDockerBackend is used.  Defaults to
            'gpuci/miniconda-cuda:11.4-runtime-ubuntu18.04'.
          container_name: Unused?
          private_key: Unused?
        """
        self.name = name
        self.run = run
        self.storage_mounts = {}
        self.storage_plans = {}
        self.setup = setup
        self.post_setup_fn = post_setup_fn
        self.workdir = workdir
        self.docker_image = docker_image if docker_image \
            else 'gpuci/miniconda-cuda:11.4-runtime-ubuntu18.04'
        self.container_name = container_name
        self._explicit_num_nodes = num_nodes  # Used as a scheduling constraint.
        self.num_nodes = 1 if num_nodes is None else num_nodes
        self.private_key = private_key
        self.inputs = None
        self.outputs = None
        self.estimated_inputs_size_gigabytes = None
        self.estimated_outputs_size_gigabytes = None
        self.resources = None
        self.time_estimator_func = None
        self.file_mounts = None
        # Filled in by the optimizer.  If None, this Task is not planned.
        self.best_resources = None

        # Block some of the clouds.
        self.blocked_clouds = set()

        # Semantics.
        if num_nodes is not None and num_nodes > 1 and isinstance(
                self.run, str):
            # The same command str for all nodes.
            self.run = lambda ips: {ip: run for ip in ips}

        dag = sky.DagContext.get_current_dag()
        dag.add(self)

    @staticmethod
    def from_yaml(yaml_path):
        with open(os.path.expanduser(yaml_path), 'r') as f:
            config = yaml.safe_load(f)

        # TODO: perform more checks on yaml and raise meaningful errors.

        task = Task(
            config.get('name'),
            run=config.get('run'),
            workdir=config.get('workdir'),
            setup=config.get('setup'),
            num_nodes=config.get('num_nodes'),
        )

        file_mounts = config.get('file_mounts')
        if file_mounts is not None:
            task.set_file_mounts(file_mounts)

        storages = config.get('storage')
        task_storages = {}
        if storages is not None:
            task_storages = {}
            if isinstance(storages, dict):
                storages = [storages]
            for storage in storages:
                name = storage.get('name')
                source = storage.get('source')
                force_stores = storage.get('force_stores')
                assert name and source, \
                       'Storage Object needs name and source path specified.'
                persistent = True if storage.get(
                    'persistent') is None else storage['persistent']
                task_storages[name] = storage_lib.Storage(name=name,
                                                          source=source,
                                                          persistent=persistent)
                if force_stores is not None:
                    assert set(force_stores) <= {'s3', 'gcs', 'azure_blob'}
                    for cloud_type in force_stores:
                        if cloud_type == 's3':
                            task_storages[name].get_or_copy_to_s3()
                        elif cloud_type == 'gcs':
                            task_storages[name].get_or_copy_to_gcs()
                        elif cloud_type == 'azure_blob':
                            task_storages[name].get_or_copy_to_azure_blob()

        storage_mounts = config.get('storage_mounts')
        if storage_mounts is not None:
            task_storage_mounts = {}
            if isinstance(storage_mounts, dict):
                storage_mounts = [storage_mounts]
            for storage_mount in storage_mounts:
                name = storage_mount.get('storage')
                storage_mount_path = storage_mount.get('mount_path')
                assert name, \
                    'Storage mount must have name reference to Storage object.'
                assert storage_mount_path, \
                    'Storage mount path cannot be empty.'
                storage = task_storages[name]
                task_storage_mounts[storage] = storage_mount_path
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
        if resources.get('cloud') is not None:
            resources['cloud'] = CLOUD_REGISTRY[resources['cloud']]
        if resources.get('accelerators') is not None:
            resources['accelerators'] = resources['accelerators']
        if resources.get('accelerator_args') is not None:
            resources['accelerator_args'] = dict(resources['accelerator_args'])
        if resources.get('use_spot') is not None:
            resources['use_spot'] = resources['use_spot']
        resources = sky.Resources(**resources)
        task.set_resources({resources})
        return task

    def validate_config(self):
        if bool(self.docker_image) != bool(self.container_name):
            raise ValueError('Either docker_image and container_name are both'
                             ' None or valid strings.')
        if self.num_nodes <= 0:
            raise ValueError('Must set Task.num_nodes to >0.')

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
            assert False, 'cloud path not supported: {}'.format(self.inputs)

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

    def set_storage_mounts(self,
                           storage_mounts: Dict[storage_lib.Storage, str]):
        """Sets the storage mounts for this Task

        Advanced method for users. Storage mounts map a Storage object
        (see sky/data/storage.py) to a mount path on the Cloud VM.
        The storage object can be from a local folder or from an existing
        cloud bucket.

        Example:
            task.set_storage_mounts({
                Storage(name='imagenet', source='s3://imagenet-bucket'): \
                '/tmp/imagenet/',
            })

        Args:
            storage_mounts: a dict of {Storage : mount_path}, where mount_path
            is the path on the Cloud VM where the Storage object will be
            mounted on
        """
        self.storage_mounts = storage_mounts
        return self

    def add_storage_mounts(self) -> None:
        """Adds storage mounts to the Storage object
        """
        # Hack: Hardcode storage_plans to AWS for optimal plan
        # Optimizer is supposed to choose storage plan but we
        # move this here temporarily
        for store in self.storage_mounts.keys():
            if len(store.stores) == 0:
                self.storage_plans[store] = storage_lib.StorageType.S3
                store.get_or_copy_to_s3()
            else:
                # Sky will download the first store that is added to remote
                self.storage_plans[store] = list(store.stores.keys())[0]

        storage_mounts = self.storage_mounts
        storage_plans = self.storage_plans
        for store, mnt_path in storage_mounts.items():
            storage_type = storage_plans[store]
            if storage_type is storage_lib.StorageType.S3:
                # TODO: allow for Storage mounting of different clouds
                self.update_file_mounts({'~/.aws': '~/.aws'})
                self.update_file_mounts({
                    mnt_path: 's3://' + store.name,
                })
            elif storage_type is storage_lib.StorageType.GCS:
                self.update_file_mounts({
                    mnt_path: 'gs://' + store.name,
                })
                assert False, 'TODO: GCS Authentication not done'
            elif storage_type is storage_lib.StorageType.AZURE:
                assert False, 'TODO: Azure Blob not mountable yet'
            else:
                raise ValueError(f'Storage Type {storage_type} \
                    does not exist!')

    def set_file_mounts(self, file_mounts: Dict[str, str]):
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
          file_mounts: a dict of { remote_path: local_path/cloud URI }, where
            remote is the VM on which this Task will eventually run on, and
            local is the node from which the task is launched.
        """
        for target, source in file_mounts.items():
            if target.endswith('/') or source.endswith('/'):
                raise ValueError(
                    'File mount paths cannot end with a slash '
                    '(try "/mydir: /mydir" or "/myfile: /myfile"). '
                    f'Found: target={target} source={source}')
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

    def set_blocked_clouds(self, blocked_clouds: Set[clouds.Cloud]):
        """Sets the clouds that this task should not run on."""
        self.blocked_clouds = blocked_clouds
        return self

    def get_local_to_remote_file_mounts(self) -> Optional[Dict[str, str]]:
        """Returns file mounts of the form (dst=VM path, src=local path).

        Any cloud object store URLs (gs://, s3://, etc.), either as source or
        destination, are not included.
        """
        if self.file_mounts is None:
            return None
        d = {}
        for k, v in self.file_mounts.items():
            if not _is_cloud_store_url(k) and not _is_cloud_store_url(v):
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
            if not _is_cloud_store_url(k) and _is_cloud_store_url(v):
                d[k] = v
        return d

    def __rshift__(self, b):
        sky.DagContext.get_current_dag().add_edge(self, b)

    def __repr__(self):
        if self.name:
            return self.name
        if isinstance(self.run, str):
            run_msg = self.run.replace('\n', '\\n')
        else:
            run_msg = '<fn>'
        if len(run_msg) > 20:
            s = 'Task(run=\'{}...\')'.format(run_msg[:20])
        else:
            s = 'Task(run=\'{}\')'.format(run_msg)
        if self.inputs is not None:
            s += '\n  inputs: {}'.format(self.inputs)
        if self.outputs is not None:
            s += '\n  outputs: {}'.format(self.outputs)
        s += '\n  resources: {}'.format(self.resources)
        return s
