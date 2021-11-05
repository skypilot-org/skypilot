from typing import Dict, Optional, Set, Union
import urllib.parse

import sky
from sky import clouds
from sky import resources

Resources = resources.Resources


def _is_cloud_store_url(url):
    result = urllib.parse.urlsplit(url)
    # '' means non-cloud URLs.
    return result.netloc


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
            private_key="~/.ssh/sky-key",
            run=None,
            args=None,  # TODO: consider removing.
    ):
        self.name = name
        self.best_resources = None
        # The script and args to run.
        self.run = run
        self.args = args
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
        #self.validate_config()

        dag = sky.DagContext.get_current_dag()
        dag.add(self)

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
        s = 'Task(run={}, args={})'.format(self.run, self.args)
        s += '\n  inputs: {}'.format(self.inputs)
        s += '\n  outputs: {}'.format(self.outputs)
        s += '\n  resources: {}'.format(self.resources)
        return s
