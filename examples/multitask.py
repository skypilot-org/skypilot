import sky
"""
with sky.Dag() as dag:
    task = sky.ParTask([
        sky.Task(run=f'echo {i}; sleep 5').set_resources(
            sky.Resources(accelerators={'K80': 0.05})) for i in range(100)
    ])
    # Share the total resources among the inner Tasks.  The inner Tasks will be
    # bin-packed and scheduled according to their individual demands.
    total = sky.Resources(accelerators={'K80': 1})
    task.set_resources(total)
"""

with sky.Dag() as dag:
    task = []
    for acc in [{'K80': 1}, {'V100': 1}]:
        task.append(
            sky.Task(run='nvidia-smi').set_resources(
                sky.Resources(sky.clouds.AWS(), accelerators=acc)))

# dag = sky.Optimizer.optimize(dag)
# # sky.execute(dag, dryrun=True)
# sky.execute(dag)

sky.launch_multitask(dag, cluster_name=None, detach_run=True)
