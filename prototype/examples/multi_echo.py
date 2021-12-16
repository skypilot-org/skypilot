import sky

with sky.Dag() as dag:
    task = sky.ParTask([
        sky.Task(run=f'echo {i}; sleep 5').set_resources(
            sky.Resources(accelerators={'K80': 0.05})) for i in range(16)
    ])

    # Share the total resources among the inner Tasks.  The inner Tasks will be
    # bin-packed and scheduled according to their individual demands.
    total = sky.Resources(accelerators={'K80': 1})
    task.set_resources(total)

sky.execute(dag, cluster_name='sky-4a4c-zongheng')
