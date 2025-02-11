import copy
import random
import sys
from typing import Dict, Tuple

import numpy as np
import pandas as pd

import sky
from sky import clouds
from sky import exceptions
from sky.clouds import service_catalog
from sky.utils import registry

ALL_INSTANCE_TYPE_INFOS = sum(
    sky.list_accelerators(gpus_only=True).values(), [])

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
    single_node_task_ids = random.choices(list(range(num_tasks)),
                                          k=num_tasks // 2)
    with sky.Dag() as dag:
        for i in range(num_tasks):
            op = sky.Task(name=f'task{i}')
            task_runtime = random.random() * max_task_runtime
            op.set_time_estimator(lambda _: task_runtime)
            op.num_nodes = random.randint(2, max_num_nodes)
            if i in single_node_task_ids:
                op.num_nodes = 1

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
            candidate_instance_types = random.choices(
                ALL_INSTANCE_TYPE_INFOS, k=len(ALL_INSTANCE_TYPE_INFOS))

            candidate_resources = set()
            for candidate in candidate_instance_types:
                instance_type = candidate.instance_type
                if pd.isna(instance_type):
                    assert candidate.cloud == 'GCP', candidate
                    (instance_list,
                     _) = service_catalog.get_instance_type_for_accelerator(
                         candidate.accelerator_name,
                         candidate.accelerator_count,
                         clouds='gcp')
                    assert instance_list, (candidate, instance_list)
                    instance_type = random.choice(instance_list)
                    if 'tpu' in candidate.accelerator_name:
                        instance_type = 'TPU-VM'
                resources = sky.Resources(
                    cloud=registry.CLOUD_REGISTRY.from_str(candidate.cloud),
                    instance_type=instance_type,
                    accelerators={
                        candidate.accelerator_name: candidate.accelerator_count
                    })
                if not resources.get_valid_regions_for_launchable():
                    continue
                requested_features = set()
                if op.num_nodes > 1:
                    requested_features.add(
                        clouds.CloudImplementationFeatures.MULTI_NODE)
                try:
                    resources.cloud.check_features_are_supported(
                        resources, requested_features)
                except exceptions.NotSupportedError:
                    continue
                candidate_resources.add(resources)
                if len(candidate_resources) >= num_candidates:
                    break
            op.set_resources(candidate_resources)
    return dag


def find_min_objective(
        dag: sky.Dag,
        minimize_cost: bool) -> Tuple[float, Dict[sky.Task, sky.Resources]]:
    """Manually finds the minimum objective value."""
    graph = dag.get_graph()
    topo_order = dag.tasks

    final_plan = {}
    min_objective = np.inf
    resources_stack = []

    def _optimize_by_brute_force(tasks, plan):
        """Optimizes a Sky DAG in a brute-force manner."""
        # NOTE: Here we assume that the Sky DAG is topologically sorted.
        nonlocal final_plan, min_objective
        task = tasks[0]
        for resources in task.resources:
            assert task.name in DUMMY_NODES or resources.is_launchable()
            plan[task] = resources
            resources_stack.append(resources)
            if len(tasks) == 1:
                if minimize_cost:
                    objective = sky.Optimizer._compute_total_cost(
                        graph, topo_order, plan)
                else:
                    objective = sky.Optimizer._compute_total_time(
                        graph, topo_order, plan)
                if objective < min_objective:
                    final_plan = {
                        topo_order[i]: resources_stack[i]
                        for i in range(len(topo_order))
                    }
                    min_objective = objective
            else:
                _optimize_by_brute_force(tasks[1:], plan)
            resources_stack.pop()

    _optimize_by_brute_force(topo_order, {})
    return min_objective, final_plan


def compare_optimization_results(dag: sky.Dag, minimize_cost: bool):
    copy_dag = copy.deepcopy(dag)
    if minimize_cost:
        optimizer_plan = sky.Optimizer._optimize_dag(dag,
                                                     sky.OptimizeTarget.COST)
    else:
        optimizer_plan = sky.Optimizer._optimize_dag(dag,
                                                     sky.OptimizeTarget.TIME)
    if minimize_cost:
        objective = sky.Optimizer._compute_total_cost(dag.get_graph(),
                                                      dag.tasks, optimizer_plan)
    else:
        objective = sky.Optimizer._compute_total_time(dag.get_graph(),
                                                      dag.tasks, optimizer_plan)

    min_objective, bf_plan = find_min_objective(copy_dag, minimize_cost)
    print('=== optimizer plan ===', file=sys.stderr)
    print(optimizer_plan, file=sys.stderr)
    print('=== brute force ===', file=sys.stderr)
    print(bf_plan, file=sys.stderr)
    # We use $1 as the tolerance for the objective value, since there can be
    # floating point precision issues.
    assert abs(objective - min_objective) < 1


def test_optimizer(enable_all_clouds):
    for seed in range(3):
        dag = generate_random_dag(num_tasks=5, seed=seed)
        sky.Optimizer._add_dummy_source_sink_nodes(dag)

        # TODO(tian): Add test for minimize_cost=False. We need a time estimator
        # that dependent on the resources, rather than returns a constant.
        compare_optimization_results(dag, minimize_cost=True)
