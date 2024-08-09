import apex

with apex.Dag() as dag:
    # The run command will be run on *all* nodes.
    # Should see two lines:
    #   My hostname: <host1>
    #   My hostname: <host2>
    apex.Task(run='echo My hostname: $(hostname)',
             num_nodes=2).set_resources(apex.Resources(apex.AWS()))

apex.launch(dag)
