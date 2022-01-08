import sky

# Create the cluster.
with sky.Dag() as dag:
    cluster_resources = sky.Resources(sky.AWS(), accelerators={'K80': 1})
    task = sky.Task().set_resources(cluster_resources)
# `detach_run` will only detach the `run` command. The provision and `setup` are
# still blocking.
handle = sky.execute(dag, cluster_name='multi_echo', detach_run=True)

# Run the multiple tasks.
for i in range(16):
    with sky.Dag() as dag:
        task = sky.Task(run=f'echo {i}; sleep 15')
        resources = sky.Resources(accelerators={'K80': 0.1})
        task.set_resources(resources)
    # FIXME: Simplify this API.
    sky.execute(dag,
                cluster_name='multi_echo',
                handle=handle,
                stages=[
                    sky.execution.Stage.EXEC,
                ],
                detach_run=True)
