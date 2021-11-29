import os
from typing import Callable, Dict, List, Optional, Set, Union
from urllib import parse
import yaml

import sky
from sky import clouds
from sky import resources

Resources = resources.Resources
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
            run: CommandOrCommandGen = None,
            workdir: Optional[str] = None,
            num_nodes: Optional[int] = None,
            # Advanced:
            post_setup_fn: Optional[CommandGen] = None,
            docker_image: Optional[str] = None,
            container_name: Optional[str] = None,
            private_key: Optional[str] = '~/.ssh/sky-key',
    ):
        """Initializes a Task.

        All fields are optional except 'run': either a shell command to run
        (str) or a command generator for different nodes (lambda; see below).

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
            In effect when LocalDockerBackend is used.  Defaults to 'ubuntu'.
          container_name: Unused?
          private_key: Unused?
        """
        self.name = name
        self.best_resources = None
        self.run = run
        self.setup = setup
        self.post_setup_fn = post_setup_fn
        self.workdir = workdir
        self.docker_image = docker_image if docker_image else 'ubuntu'
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

        # The resources failed to be provisioned.
        self.blocked_resources = set()
        # Block some of the clouds.
        self.blocked_clouds = set()

        # Semantics.
        if num_nodes is not None and num_nodes > 1 and type(self.run) is str:
            # The same command str for all nodes.
            self.run = lambda ips: {ip: run for ip in ips}

        dag = sky.DagContext.get_current_dag()
        dag.add(self)

    @staticmethod
    def from_yaml(yaml_path):
        with open(os.path.expanduser(yaml_path), 'r') as f:
            config = yaml.safe_load(f)
        # TODO: perform more checks on yaml and raise meaningful errors.
        if 'run' not in config:
            raise ValueError('The YAML spec should include a \'run\' field.')

        task = Task(
            config.get('name'),
            run=config['run'],  # Required field.
            workdir=config.get('workdir'),
            setup=config.get('setup'),
            num_nodes=config.get('num_nodes'),
        )

        file_mounts = config.get('file_mounts')
        if file_mounts is not None:
            task.set_file_mounts(file_mounts)

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
        return

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
        assert type(self.inputs) is str, self.inputs
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

    def set_file_mounts(self, file_mounts: Dict[str, str]):
        """Sets the file mounts for this Task.

        File mounts are local files/dirs to be synced to specific paths on the
        remote VM(s) where this Task will run.  Can be used for syncing
        datasets, dotfiles, etc.

        Example:

            task.set_file_mounts({
                '~/.dotfile': '/local/.dotfile',
                '/remote/dir': '/local/dir',
            })

        Args:
          file_mounts: a dict of { remote_path: local_path }, where remote is
            the VM on which this Task will eventually run on, and local is the
            node from which the task is launched.
        """
        self.file_mounts = file_mounts
        return self

    def set_blocked_clouds(self, clouds: Set[clouds.Cloud]):
        """Sets the clouds that this task should not run on."""
        self.blocked_clouds = clouds
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

    def __rshift__(a, b):
        sky.DagContext.get_current_dag().add_edge(a, b)

    def __repr__(self):
        if self.name:
            return self.name
        s = 'Task(run=\'{}\')'.format(self.run)
        if self.inputs is not None:
            s += '\n  inputs: {}'.format(self.inputs)
        if self.outputs is not None:
            s += '\n  outputs: {}'.format(self.outputs)
        s += '\n  resources: {}'.format(self.resources)
        return s


class ParTask(Task):
    """ParTask: a wrapper of independent Tasks to be run in parallel.

    ParTask enables multiple Tasks to be run in paralel, while sharing the same
    total resources (VMs).

    Typical usage: use a ParTask to wrap hyperparameter tuning trials.

        per_trial_resources = ...
        total_resources = ...

        par_task = sky.ParTask([
            sky.Task(
                run=f'python app.py -s={i}').set_resources(per_trial_resources)
            for i in range(10)
        ])

        # Provision and share a total of this many resources.  Inner Tasks will
        # be bin-packed and scheduled according to their demands.
        par_task.set_resources(total_resources)

    Semantics:

    (1) A ParTask inherits the following fields from its inner Tasks:

        setup
        workdir
        num_nodes == 1

    Thus, all inner Tasks are required to have identical values for these
    fields.  This will be checked when constructing the ParTask().

    These fields can be distinct across the inner Tasks:

        resources (e.g., some Tasks requiring more than others)

    TODO: what about
      convenience func:
        set_file_mounts()     (in principle we can try to merge)
      used by optimizer:
        set_time_estimator()  (in principle we can try to merge)
        set_inputs()
        set_outputs()

    (2) ParTask.set_resources(...) must be called, providing the total
    resources to share among all tasks.

    TODO: allow an option to make this optional, which should have the
    semantics "use as many resources as required".
    """

    def __init__(self, tasks: List[Task]):
        super().__init__()
        # Validation.
        assert all([isinstance(task, Task) and \
                    not isinstance(task, ParTask) for task in tasks]), \
                    'ParTask can only wrap base Tasks.'
        assert all([task.num_nodes == 1 for task in tasks]), \
            'ParTask currently only wraps Tasks with num_nodes == 1.'

        setup = set([task.setup for task in tasks])
        assert len(setup) == 1, 'Inner Tasks must have the same \'setup\'.'
        self.setup = list(setup)[0]

        workdir = set([task.workdir for task in tasks])
        assert len(workdir) == 1, 'Inner Tasks must have the same \'workdir\'.'
        self.workdir = list(workdir)[0]

        # TODO: No support for these yet.
        assert all([task.file_mounts is None for task in tasks])
        assert all([task.inputs is None for task in tasks])
        assert all([task.outputs is None for task in tasks])
        assert all([task.time_estimator_func is None for task in tasks])

        dag = sky.DagContext.get_current_dag()
        for task in tasks:
            dag.remove(task)
        self.tasks = tasks

    def get_task_resource_demands(self,
                                  task_i: int) -> Optional[Dict[str, int]]:
        """Gets inner Task i's resource demands, useful for scheduling."""
        task = self.tasks[task_i]
        r = task.resources
        if r is None:
            return None
        assert len(r) == 1, \
            'Inner Tasks must not have multiple Resources choices.'
        r = list(r)[0]
        # For now we only count accelerators as resource demands.
        demands = r.get_accelerators()
        return demands

    def __repr__(self):
        if self.name:
            return self.name
        s = 'ParTask({} tasks)'.format(len(self.tasks))
        s += '\n  resources: {}'.format(self.resources)
        return s
