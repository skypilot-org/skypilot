import sky

with sky.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    task = sky.Task.from_yaml('examples/resnet_app.yaml')

# sky.execute(dag, dryrun=True)
sky.execute(dag)
