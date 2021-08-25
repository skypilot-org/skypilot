"""Assumptions:

- Users supply a estimate_runtime_func() for all nodes in the DAG.
- Assuming homogeneous instances:
  - Support for choosing # instances
- Support for heterogeneous instances?


Optimization problem:
 - min cost, subject to some constraints?
 - min latency, subject to some constraints?

DAG assumption: chain.  If multiple branches, take into account of parallelism?

Incorporate the notion of region/zone (affects pricing).
Incorporate the notion of per-account egress quota (affects pricing).
"""
import collections
import copy
import enum
import pprint
import typing

import networkx as nx
import numpy as np


class Cloud(object):

    # TODO: incorporate region/zone into the API.
    def instance_type_to_hourly_cost(self, instance_type):
        """Returns the hourly on-demand price for an instance type."""
        raise NotImplementedError

    def get_egress_cost(self, num_gigabytes):
        """Returns the egress cost.

        TODO: takes into account "per month" accumulation per account.
        """
        raise NotImplementedError

    def is_same_cloud(self, other):
        raise NotImplementedError


class AWS(Cloud):
    _REPR = 'AWS'

    # In general, query this from the cloud.
    _ON_DEMAND_PRICES = {
        # p3.
        'p3.2xlarge': 3.06,
        'p3.8xlarge': 12.24,
        'p3.16xlarge': 24.48,

        # inf1.
        'inf1.xlarge': 0.228,
        'inf1.2xlarge': 0.362,
        'inf1.6xlarge': 1.18,
        'inf1.24xlarge': 4.721,
    }

    def instance_type_to_hourly_cost(self, instance_type):
        return AWS._ON_DEMAND_PRICES[instance_type]

    def get_egress_cost(self, num_gigabytes):
        # In general, query this from the cloud:
        #   https://aws.amazon.com/s3/pricing/
        # NOTE: egress from US East (Ohio).
        if num_gigabytes > 150 * 1024:
            return 0.05 * num_gigabytes
        cost = 0.0
        if num_gigabytes >= 50 * 1024:
            cost += (num_gigabytes - 50 * 1024) * 0.07
            num_gigabytes -= 50 * 1024

        if num_gigabytes >= 10 * 1024:
            cost += (num_gigabytes - 10 * 1024) * 0.085
            num_gigabytes -= 10 * 1024

        if num_gigabytes > 1:
            cost += (num_gigabytes - 1) * 0.09
            num_gigabytes -= 1

        cost += 0.0
        return cost

    def __repr__(self):
        return AWS._REPR

    def is_same_cloud(self, other):
        return isinstance(other, AWS)


class GCP(Cloud):

    _REPR = 'GCP'

    # In general, query this from the cloud.
    # NOTE: assumes us-central1.
    _ON_DEMAND_PRICES = {
        # GPUs: https://cloud.google.com/compute/gpus-pricing.
        '1x V100': 2.48,
        '2x V100': 2.48 * 2,
        '4x V100': 2.48 * 4,
        '8x V100': 2.48 * 8,
        # TPUs: https://cloud.google.com/tpu/pricing.
        'tpu-v2-8': 4.5,
        'tpu-v3-8': 8.0,
        # VMs: https://cloud.google.com/compute/all-pricing.
        'n1-standard-8': 0.379998,
    }

    def instance_type_to_hourly_cost(self, instance_type):
        return GCP._ON_DEMAND_PRICES[instance_type]

    def get_egress_cost(self, num_gigabytes):
        # In general, query this from the cloud:
        #   https://cloud.google.com/storage/pricing#network-pricing
        # NOTE: egress to worldwide (excl. China, Australia).
        if num_gigabytes <= 1024:
            return 0.12 * num_gigabytes
        elif num_gigabytes <= 1024 * 10:
            return 0.11 * num_gigabytes
        else:
            return 0.08 * num_gigabytes

    def __repr__(self):
        return GCP._REPR

    def is_same_cloud(self, other):
        return isinstance(other, GCP)


class Azure(Cloud):
    pass


class Hardware(typing.NamedTuple):
    cloud: Cloud
    types: typing.Tuple[str]

    def __repr__(self) -> str:
        return f'{self.cloud}({self.types})'
        # return f'{self.cloud.name}({self.types})'

    def get_cost(self, seconds):
        """Returns cost in USD for the runtime in seconds."""

        cost = 0.0
        typs = self.types
        if type(typs) is str:
            typs = [typs]
        for instance_type in typs:
            hourly_cost = self.cloud.instance_type_to_hourly_cost(instance_type)
            cost += hourly_cost * (seconds / 3600)
        return cost


# DAGs.
class DagContext(object):
    _current_dag = None
    _previous_dags = []

    @classmethod
    def push_dag(cls, dag):
        if cls._current_dag:
            cls._previous_dags.append(cls._current_dag)
        cls._current_dag = dag

    @classmethod
    def pop_dag(cls):
        old_dag = cls._current_dag
        if cls._previous_dags:
            cls._current_dag = cls._previous_dags.pop()
        else:
            cls._current_dag = None
        return old_dag

    @classmethod
    def get_current_dag(cls):
        return cls._current_dag


class Dag(object):
    """FIXME: assume a chain DAG for now."""

    _PREVIOUS_DAGS = []
    _CURRENT_DAG = None

    def __init__(self):
        self.operators = []
        self.graph = nx.DiGraph()

    def add(self, operator):
        self.graph.add_node(operator)
        self.operators.append(operator)

    def add_edge(self, op1, op2):
        assert op1 in self.graph.nodes
        assert op2 in self.graph.nodes
        self.graph.add_edge(op1, op2)

    def __enter__(self):
        DagContext.push_dag(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        DagContext.pop_dag()

    def __repr__(self):
        pformat = pprint.pformat(self.operators)
        return 'DAG:\n{}'.format(pformat)
        # return '<DAG=[{}]>'.format(','.join(map(str, self.operators)))

    def get_graph(self):
        return self.graph


class PlannedDag(object):
    pass


# Optimizer.
class SkyOptimizer(object):

    # Constants: minimize what target?
    COST = 0
    TIME = 1

    @staticmethod
    def _egress_cost(src_cloud, dst_cloud, gigabytes):
        if not src_cloud.is_same_cloud(dst_cloud):
            egress_cost = src_cloud.get_egress_cost(num_gigabytes=gigabytes)
        else:
            egress_cost = 0.0
        if egress_cost > 0:
            print('  {} -> {} egress cost: ${} for {:.1f} GB'.format(
                src_cloud, dst_cloud, egress_cost, gigabytes))
        return egress_cost

    @staticmethod
    def optimize(dag: Dag, minimize) -> PlannedDag:
        if minimize == SkyOptimizer.COST:
            return SkyOptimizer._optimize_cost(dag)
        elif minimize == SkyOptimizer.TIME:
            return SkyOptimizer._optimize_time(dag)
        else:
            assert False, 'Set minimize to COST or TIME.'

    @staticmethod
    def _optimize_cost(dag: Dag):
        graph = dag.get_graph()
        topo_order = list(nx.topological_sort(graph))

        # FIXME: rewrite to (node, cloud) only as egress cost depends only on
        # clouds.
        # node -> {hardware -> best estimated cost}
        dp_best_cost = collections.defaultdict(dict)
        # node -> {hardware -> (parent, best parent hardware)}
        dp_point_backs = collections.defaultdict(dict)

        # Initialize.
        # TODO: introduce a Src node and fold this into main loop.
        source = topo_order[0]
        print('#### {} ####'.format(source))
        for hardware in source.get_allowed_hardware():
            # Estimates a runtime and converts to $.
            print('hardware:', hardware)
            estimated_runtime = source.estimate_runtime(hardware)
            print('  estimated_runtime: {:.0f} s ({:.1f} hr)'.format(
                estimated_runtime, estimated_runtime / 3600))
            estimated_cost = hardware.get_cost(estimated_runtime)
            print(
                '  estimated_cost (no egress): ${:.1f}'.format(estimated_cost))
            egress_cost = SkyOptimizer._egress_cost(
                source.get_inputs_cloud(), hardware.cloud,
                source.get_estimated_inputs_size_gigabytes())
            if egress_cost > 0:
                estimated_cost += egress_cost
                print('  estimated_cost (w/ egress): ${:.1f}'.format(
                    estimated_cost))
            dp_best_cost[source][hardware] = estimated_cost

        # Transitions.
        for node in topo_order[1:]:
            print('#### {} ####'.format(node))
            parents = list(graph.predecessors(node))
            assert len(parents) == 1, 'Supports single parent for now'
            parent = parents[0]

            for hardware in node.get_allowed_hardware():
                # Computes dp_best_cost[node][hardware]
                #   = my estimated cost + min { pred_cost + egress_cost }
                assert hardware not in dp_best_cost[node]
                print('hardware:', hardware)

                estimated_runtime = node.estimate_runtime(hardware)
                estimated_cost = hardware.get_cost(estimated_runtime)
                min_pred_cost_plus_egress = np.inf
                print('  estimated_runtime: {:.0f} s ({:.1f} hr)'.format(
                    estimated_runtime, estimated_runtime / 3600))
                print('  estimated_cost (no egress): ${:.1f}'.format(
                    estimated_cost))

                for parent_hardware, parent_cost in dp_best_cost[
                        parent].items():
                    egress_cost = SkyOptimizer._egress_cost(
                        parent_hardware.cloud, hardware.cloud,
                        parent.get_estimated_outputs_size_gigabytes())
                    if parent_cost + egress_cost < min_pred_cost_plus_egress:
                        min_pred_cost_plus_egress = parent_cost + egress_cost
                        best_parent = parent_hardware
                        best_egress_cost = egress_cost
                print('  best_parent', best_parent)
                print('  best_egress_cost', best_egress_cost)
                dp_point_backs[node][hardware] = (parent, best_parent, best_egress_cost)

                dp_best_cost[node][hardware] = min_pred_cost_plus_egress

        print('\nOptimizer - dp_best_cost:')
        pprint.pprint(dict(dp_best_cost))

        return SkyOptimizer.make_optimized_plan(dag, dp_best_cost, topo_order,
                                                dp_point_backs)

    @staticmethod
    def make_optimized_plan(logical_dag, dp_best_cost, topo_order,
                            dp_point_backs):
        # FIXME: this assumes chain.
        print('\nOptimizer - best plan:')
        node = topo_order[-1]
        messages = []
        egress_cost = 0.0
        while True:
            best_costs = dp_best_cost[node]
            h, c = None, np.inf

            for hardware in best_costs:
                if best_costs[hardware] < c:
                    h = hardware
                    c = best_costs[hardware]

            # TODO: avoid modifying in-place.
            node.best_hardware = h
            # if egress_cost:
            #     messages.append('       egress: cost ${}'.format(egress_cost))
            messages.append('  {} : {}'.format(node, h))
            # print('**', node, h)
            if node not in dp_point_backs:
                break
            egress_cost = dp_point_backs[node][h][2]
            node = dp_point_backs[node][h][0]

        for msg in reversed(messages):
            print(msg)


# Operator.
class Operator(object):
    """Operator: a coarse-grained stage in an application."""

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

        dag = DagContext.get_current_dag()
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
            return AWS()
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
              'Node [{}] does not have a cost model set; '\
              'call set_estimate_runtime_func() first'.format(self))
        return self.estimate_runtime_func(hardware)

    def __rshift__(a, b):
        DagContext.get_current_dag().add_edge(a, b)

    def __repr__(self):
        if self.name:
            return self.name
        s = 'Operator(cmd={}, args={})'.format(self.command, self.args)
        s += '\n  inputs: {}'.format(self.inputs)
        s += '\n  outputs: {}'.format(self.outputs)
        s += '\n  allowed_hardware: {}'.format(self.allowed_hardware)
        return s


def resnet50_estimate_runtime(hardware):
    """A simple runtime model for Resnet50."""
    # 3.8 G Multiply-Adds, 2 FLOPs per MADD, 3 for fwd+bwd.
    flops_for_one_image = 3.8 * (10**9) * 2 * 3

    if isinstance(hardware.cloud, AWS):
        instance = hardware.types
        if instance == 'p3.2xlarge':
            num_v100s = 1
        elif instance == 'p3.8xlarge':
            num_v100s = 4
        elif instance == 'p3.16xlarge':
            num_v100s = 8
        else:
            assert False, 'Not supported: {}'.format(hardware)

        # Adds communication overheads per step (in seconds).
        communication_slack = 0.0
        if num_v100s == 4:
            communication_slack = 0.15
        elif num_v100s == 8:
            communication_slack = 0.30

        max_per_device_batch_size = 256
        effective_batch_size = max_per_device_batch_size * num_v100s

        # 112590 steps, 1024 BS = 90 epochs.
        total_steps = 112590 * (1024.0 / effective_batch_size)
        flops_for_one_batch = flops_for_one_image * max_per_device_batch_size
        # 27 TFLOPs, harmonic mean b/t 15TFLOPs (single-precision) & 120 TFLOPs
        # (16 bit).
        utilized_flops = 27 * (10**12)
        estimated_step_time_seconds = flops_for_one_batch / utilized_flops \
          + communication_slack
        estimated_run_time_seconds = estimated_step_time_seconds * total_steps

    elif isinstance(hardware.cloud, GCP):
        assert 'tpu-v3-8' in hardware.types, hardware
        tpu_v3_8_flops = 420 * (10**12)
        known_resnet50_utilization = 0.445  # From actual profiling.

        max_per_device_batch_size = 1024
        total_steps = 112590  # 112590 steps, 1024 BS = 90 epochs.
        flops_for_one_batch = flops_for_one_image * max_per_device_batch_size
        utilized_flops = tpu_v3_8_flops * known_resnet50_utilization
        estimated_step_time_seconds = flops_for_one_batch / utilized_flops
        estimated_run_time_seconds = estimated_step_time_seconds * total_steps

        print('  tpu-v3-8 estimated_step_time_seconds',
              estimated_step_time_seconds)

    else:
        assert False, 'not supported cloud in prototype: {}'.format(
            hardware.cloud)

    return estimated_run_time_seconds


def resnet50_infer_estimate_runtime(hardware):
    return 0.0  # FIXME


def make_application():
    """A simple application: train_op -> infer_op."""

    with Dag() as dag:
        # Train.
        train_op = Operator('train_op',
                            command='train.py',
                            args='--data_dir=INPUTS[0] --model_dir=OUTPUTS[0]')

        train_op.set_inputs('s3://my-imagenet-data',
                            estimated_size_gigabytes=150)

        # 'CLOUD': saves to the cloud this op ends up executing on.
        train_op.set_outputs('CLOUD://my-model', estimated_size_gigabytes=0.1)

        train_op.set_allowed_hardware({
            Hardware(AWS(), 'p3.2xlarge'),  # 1 V100, EC2.
            Hardware(AWS(), 'p3.8xlarge'),  # 4 V100s, EC2.
            # Tuples mean all resources are required.
            # Hardware(GCP(), ('n1-standard-8', 'tpu-v3-8')),
        })

        train_op.set_estimate_runtime_func(resnet50_estimate_runtime)

        # Infer.
        infer_op = Operator('infer_op',
                            command='infer.py',
                            args='--model_dir=INPUTS[0]')

        # Data dependency.
        # FIXME: make the system know this is from train_op's outputs.
        infer_op.set_inputs(train_op.get_outputs(),
                            estimated_size_gigabytes=0.1)

        infer_op.set_allowed_hardware({
            Hardware(AWS(), 'inf1.xlarge'),
            # Hardware(GCP(), ('n1-standard-8', '1x V100')),
        })

        infer_op.set_estimate_runtime_func(resnet50_infer_estimate_runtime)

        # Chain the operators (Airflow syntax).
        # The dependency represents data flow.
        train_op >> infer_op

    return dag


dag = make_application()

SkyOptimizer.optimize(dag, minimize=SkyOptimizer.COST)

# SkyOptimizer.optimize(dag, minimize=SkyOptimizer.TIME)

# print(train_op)
# print('######## DAG')
# print(dag)
# print('######## nodes')
# print(dag.get_graph().nodes)
# print(plan)
