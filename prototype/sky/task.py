from typing import Dict, List, Optional, Set, Union
import os
import urllib.parse
import uuid
import yaml

import sky
from sky import clouds
from sky import resources

Resources = resources.Resources

CLOUD_REGISTRY = {
    'aws': clouds.AWS(),
    'gcp': clouds.GCP(),
    'azure': clouds.Azure(),
}


def _is_cloud_store_url(url):
    result = urllib.parse.urlsplit(url)
    # '' means non-cloud URLs.
    return result.netloc


def _dict_to_cmd_args(d):
    return ' '.join(['--{}={}'.format(k, v) for k, v in d.items()])


class Task(object):
    """Task: a coarse-grained stage in an application."""

    def __init__(
            self,
            name=None,
            workdir=None,
            setup=None,
            post_setup_fn=None,
            docker_image=None,
            container_name=None,
            num_nodes=1,
            private_key='~/.ssh/sky-key',
            run=None,
    ):
        self.name = name
        self.best_resources = None
        self.run = run
        self.setup = setup
        self.post_setup_fn = post_setup_fn
        self.workdir = workdir
        self.docker_image = docker_image
        self.container_name = container_name
        self.num_nodes = num_nodes
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

        # Check for proper assignment of Task variables
        # self.validate_config()

        dag = sky.DagContext.get_current_dag()
        dag.add(self)

    @staticmethod
    def from_yaml(yaml_path):
        # TODO(gmittal): Investigate better config tooling: https://confuse.readthedocs.io/en/latest/.
        # TODO: Add support for command line modification

        with open(os.path.expanduser(yaml_path), 'r') as f:
            config = yaml.safe_load(f)

        workdir = config.get('workdir')

        setup = config['setup']
        run = config['run']

        if isinstance(setup, dict):
            base_cmd_args = _dict_to_cmd_args(setup.get('flags', {}))
            setup = '{}\n{} {}'.format(setup['preamble'],
                                       setup.get('base_cmd', ''), base_cmd_args)

        if isinstance(run, dict):
            base_cmd_args = _dict_to_cmd_args(run.get('flags', {}))
            run = '{}\n{} {}'.format(run['preamble'],
                                     run.get('base_cmd', '').strip(),
                                     base_cmd_args)

        file_mounts = config.get('file_mounts')
        if file_mounts is not None:
            for src, dst in file_mounts.items():
                run = run.replace(dst, src)

        task = Task(
            config['name'],
            workdir=workdir,
            setup=setup,
            run=run,
        )

        if file_mounts is not None:
            task.set_file_mounts(file_mounts)

        if config.get('input') is not None:
            inputs = config['input']
            # TODO: allow option to say (or detect) no download/egress cost.
            task.set_inputs(**inputs)

        if config.get('output') is not None:
            outputs = config['output']
            task.set_outputs(**outputs)

        resources = config['resources']
        if resources.get('cloud') is not None:
            resources['cloud'] = CLOUD_REGISTRY[resources['cloud']]
        if resources.get('accelerators') is not None:
            resources['accelerators'] = dict(resources['accelerators'])
        resources = sky.Resources(**resources)

        task.set_resources({resources})
        return task

    def validate_config(self):
        if bool(self.docker_image) != bool(self.container_name):
            raise ValueError(
                "Either docker image and container are both None or valid strings"
            )
        if self.num_nodes <= 0:
            raise ValueError("Must be >0 total nodes")
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
