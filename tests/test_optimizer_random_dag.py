import copy
import random

import numpy as np
import sky

CLOUDS = {
    'AWS': sky.AWS(),
    'GCP': sky.GCP(),
    'Azure': sky.Azure(),
}
GCP_INSTANCE_TYPES = list(sky.GCP._ON_DEMAND_PRICES.keys())

DUMMY_NODES = [
    sky.optimizer._DUMMY_SOURCE_NAME,
    sky.optimizer._DUMMY_SINK_NAME,
]


def generate_random_dag(num_tasks, seed):
    """Generates a random Sky DAG to test Sky optimizer."""
    instance_types = list(sky.list_accelerators(gpus_only=True).values())
    instance_types = sum(instance_types, [])
    random.seed(seed)

    with sky.Dag() as dag:
        for i in range(num_tasks):
            op = sky.Task(name=f'task{i}')
            execution_time = random.random() * 3600
            op.set_time_estimator(lambda _: execution_time)
            op.num_nodes = random.randint(1, 8)

            num_parents = random.randint(0, min(i, 5)) if i > 0 else 0
            if num_parents == 0:
                src_cloud = random.choice(['s3:', 'gs:', None])
                src_volume = random.randint(0, 1000)
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
            op.set_outputs('CLOUD', random.randint(0, 1000))

            num_candidates = random.randint(1, 5)
            candidate_instance_types = random.choices(instance_types,
                                                      k=num_candidates)
            op.set_resources({
                sky.Resources(
                    cloud=CLOUDS[candidate.cloud],
                    instance_type=candidate.instance_type \
                        if candidate.cloud != 'GCP' \
                        else random.choice(GCP_INSTANCE_TYPES),
                    accelerators={
                        candidate.accelerator_name: candidate.accelerator_count},
                )
                for candidate in candidate_instance_types
            })
    return dag


def find_min_objective(dag, minimize):
    """Manually finds the minimum objective value."""
    dag = sky.Optimizer._add_dummy_source_sink_nodes(dag)
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
                if minimize == sky.OptimizeTarget.COST:
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


if __name__ == '__main__':
    target = sky.OptimizeTarget.COST
    dag = generate_random_dag(num_tasks=10, seed=2)
    copy_dag = copy.deepcopy(dag)

    sky.optimize(dag, minimize=target)

    objective = find_min_objective(copy_dag, minimize=target)
    print(f'Min objective: {objective:.1f}')
