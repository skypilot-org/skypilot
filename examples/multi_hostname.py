import sky
from sky import clouds

with sky.Dag() as dag:
    # The run command will be run on *all* nodes.
    # Should see two lines:
    #   My hostname: <host1>
    #   My hostname: <host2>
    sky.Task(run='echo My hostname: $(hostname)',
             num_nodes=2).set_resources(sky.Resources(clouds.AWS()))

sky.launch(dag)
