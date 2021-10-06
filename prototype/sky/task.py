import sky
from sky import clouds


class Task(object):
    """Task: a coarse-grained stage in an application."""

    def __init__(self, name=None, command=None, args=None):
        self.name = name
        # The script and args to run.
        self.command = command
        self.args = args

        self.inputs = None
        self.outputs = None
        self.estimated_inputs_size_gigabytes = None
        self.estimated_outputs_size_gigabytes = None
        self.allowed_hardware = None
        self.estimate_runtime_func = None

        dag = sky.DagContext.get_current_dag()
        dag.add(self)

    # E.g., 's3://bucket', 'gcs://bucket', or None.
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
        elif self.inputs.startswith('gcs:'):
            return GCP()
        else:
            assert False, 'cloud path not supported: {}'.format(self.inputs)

    def set_outputs(self, outputs, estimated_size_gigabytes):
        self.outputs = outputs
        self.estimated_outputs_size_gigabytes = estimated_size_gigabytes

    def get_outputs(self):
        return self.outputs

    def get_estimated_outputs_size_gigabytes(self):
        return self.estimated_outputs_size_gigabytes

    def set_allowed_hardware(self, allowed_hardware):
        """Sets the allowed cloud-instance type combos to execute this op."""
        self.allowed_hardware = allowed_hardware

    def get_allowed_hardware(self):
        return self.allowed_hardware

    def set_estimate_runtime_func(self, func):
        """Sets a func mapping hardware to estimated time (secs)."""
        self.estimate_runtime_func = func

    def estimate_runtime(self, hardware):
        """Returns a func mapping hardware to estimated time (secs)."""
        if self.estimate_runtime_func is None:
            raise NotImplementedError(
                'Node [{}] does not have a cost model set; '
                'call set_estimate_runtime_func() first'.format(self))
        return self.estimate_runtime_func(hardware)

    def __rshift__(a, b):
        sky.DagContext.get_current_dag().add_edge(a, b)

    def __repr__(self):
        if self.name:
            return self.name
        s = 'Task(cmd={}, args={})'.format(self.command, self.args)
        s += '\n  inputs: {}'.format(self.inputs)
        s += '\n  outputs: {}'.format(self.outputs)
        s += '\n  allowed_hardware: {}'.format(self.allowed_hardware)
        return s
