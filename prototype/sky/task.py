import sky
from sky import clouds


class Task(object):
    """Task: a coarse-grained stage in an application."""

    def __init__(
            self,
            name=None,
            workdir=None,
            setup=None,
            post_setup_fn = None,
            docker_image = None,
            container_name = None,
            num_workers = 0,
            private_key = "~/.ssh/sky_key",
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
        self.num_workers = num_workers
        self.private_key = private_key

        self.inputs = None
        self.outputs = None
        self.estimated_inputs_size_gigabytes = None
        self.estimated_outputs_size_gigabytes = None
        self.resources = None
        self.time_estimator_func = None
        # Check for proper assignment of Task variables
        self.validate_config()

        dag = sky.DagContext.get_current_dag()
        dag.add(self)

    def validate_config(self):
        if bool(self.docker_image) != bool(self.container_name):
            raise ValueError("Either docker image and container are both None or valid strings")
        if self.num_workers < 0:
            raise ValueError("Worker nodes must be >=0")
        return

    # E.g., 's3://bucket', 'gs://bucket', or None.
    def set_inputs(self, inputs, estimated_size_gigabytes):
        self.inputs = inputs
        self.estimated_inputs_size_gigabytes = estimated_size_gigabytes

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

    def get_outputs(self):
        return self.outputs

    def get_estimated_outputs_size_gigabytes(self):
        return self.estimated_outputs_size_gigabytes

    def set_resources(self, resources):
        """Sets the allowed cloud-instance type combos to execute this op."""
        self.resources = resources

    def get_resources(self):
        return self.resources

    def set_time_estimator(self, func):
        """Sets a func mapping resources to estimated time (secs)."""
        self.time_estimator_func = func

    def estimate_runtime(self, resources):
        """Returns a func mapping resources to estimated time (secs)."""
        if self.time_estimator_func is None:
            raise NotImplementedError(
                'Node [{}] does not have a cost model set; '
                'call set_time_estimator() first'.format(self))
        return self.time_estimator_func(resources)

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
