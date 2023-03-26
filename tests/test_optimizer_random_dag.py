import copy
import random

import numpy as np
import pandas as pd

import sky
from sky import clouds

ALL_INSTANCE_TYPES = sum(sky.list_accelerators(gpus_only=True).values(), [])
GCP_HOST_VM = 'n1-highmem-8'

DUMMY_NODES = [
    sky.optimizer._DUMMY_SOURCE_NAME,
    sky.optimizer._DUMMY_SINK_NAME,
]


def generate_random_dag(
    num_tasks: int,
    seed: int = 0,
    max_num_nodes: int = 10,
    max_num_parents: int = 5,
    max_num_candidate_resources: int = 5,
    max_task_runtime: int = 3600,
    max_data_size: int = 1000,
) -> sky.Dag:
    """Generates a random Sky DAG to test Sky optimizer."""
    random.seed(seed)
    with sky.Dag() as dag:
        for i in range(num_tasks):
            op = sky.Task(name=f'task{i}')
            task_runtime = random.random() * max_task_runtime
            op.set_time_estimator(lambda _: task_runtime)
            op.num_nodes = random.randint(1, max_num_nodes)

            if i == 0:
                num_parents = 0
            else:
                num_parents = random.randint(0, min(i, max_num_parents))

            if num_parents == 0:
                src_cloud = random.choice(['s3:', 'gs:', None])
                src_volume = random.randint(0, max_data_size)
            else:
                parents = random.choices(dag.tasks[:-1], k=num_parents)
                for parent in parents:
                    parent >> op
                # NOTE: Sky only takes single input data source
                src_cloud = parents[0]
                # Sky uses the parent's output data size
                src_volume = None

            if src_cloud is not None:
                op.set_inputs(src_cloud, src_volume)
            op.set_outputs('CLOUD', random.randint(0, max_data_size))

            num_candidates = random.randint(1, max_num_candidate_resources)
            candidate_instance_types = random.choices(ALL_INSTANCE_TYPES,
                                                      k=num_candidates)

            candidate_resources = set()
            for candidate in candidate_instance_types:
                instance_type = candidate.instance_type
                if pd.isna(instance_type):
                    instance_type = GCP_HOST_VM
                resources = sky.Resources(
                    cloud=clouds.CLOUD_REGISTRY.from_str(candidate.cloud),
                    instance_type=instance_type,
                    accelerators={
                        candidate.accelerator_name: candidate.accelerator_count
                    })
                candidate_resources.add(resources)
            op.set_resources(candidate_resources)
    return dag


def find_min_objective(dag: sky.Dag, minimize_cost: bool) -> float:
    """Manually finds the minimum objective value."""
    graph = dag.get_graph()
    topo_order = dag.tasks

    def _optimize_by_brute_force(tasks, plan):
        """Optimizes a Sky DAG in a brute-force manner."""
        # NOTE: Here we assume that the Sky DAG is topologically sorted.
        task = tasks[0]
        min_objective = np.inf
        for resources in task.get_resources():
            assert task.name in DUMMY_NODES or resources.is_launchable()
            plan[task] = resources
            if len(tasks) == 1:
                if minimize_cost:
                    objective = sky.Optimizer._compute_total_cost(
                        graph, topo_order, plan)
                else:
                    objective = sky.Optimizer._compute_total_time(
                        graph, topo_order, plan)
            else:
                objective = _optimize_by_brute_force(tasks[1:], plan)
            if objective < min_objective:
                min_objective = objective
        return min_objective

    return _optimize_by_brute_force(topo_order, {})


def compare_optimization_results(dag: sky.Dag, minimize_cost: bool):
    copy_dag = copy.deepcopy(dag)

    optimizer_plan = sky.Optimizer._optimize_objective(dag, minimize_cost)
    if minimize_cost:
        objective = sky.Optimizer._compute_total_cost(dag.get_graph(),
                                                      dag.tasks, optimizer_plan)
    else:
        objective = sky.Optimizer._compute_total_time(dag.get_graph(),
                                                      dag.tasks, optimizer_plan)

    min_objective = find_min_objective(copy_dag, minimize_cost)
    assert abs(objective - min_objective) < 1e-3


def test_optimizer(enable_all_clouds):

    dag = generate_random_dag(num_tasks=5, seed=0)
    sky.Optimizer._add_dummy_source_sink_nodes(dag)

    compare_optimization_results(dag, minimize_cost=True)
    compare_optimization_results(dag, minimize_cost=False)
