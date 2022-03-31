import random

import numpy as np
import sky


CLOUDS = {
    'AWS': sky.AWS(),
    'GCP': sky.GCP(),
    'Azure': sky.Azure(),
}


def generate_random_dag(num_tasks, seed):
    """Generates a random Sky DAG to test Sky optimizer."""
    instance_types = list(sky.list_accelerators(gpus_only=True).values())
    instance_types = sum(instance_types, [])
    random.seed(seed)

    with sky.Dag() as dag:
        for i in range(num_tasks):
            op = sky.Task(name=f'task{i}')
            op.set_time_estimator(lambda _: 3600)

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
            candidate_instance_types = random.choices(
                instance_types, k=num_candidates)
            op.set_resources({
                sky.Resources(
                    cloud=CLOUDS[candidate.cloud],
                    instance_type=candidate.instance_type if not np.nan else None,
                    accelerators={
                        candidate.accelerator_name: candidate.accelerator_count},
                )
                for candidate in candidate_instance_types
            })
    return dag


if __name__ == '__main__':
    dag = generate_random_dag(num_tasks=100, seed=0)
    sky.optimize(dag, minimize=sky.OptimizeTarget.COST)
