import sky
from sky import clouds

import time_estimators

import yaml

CLOUD_REGISTRY = {
    'aws': clouds.AWS(),
    'gcp': clouds.GCP(),
    'azure': clouds.Azure(),
}

with sky.Dag() as dag:
    # Load the YAML file
    with open("examples/resnet.yaml", "r") as f:
        config = yaml.safe_load(f)

    # The working directory contains all code and will be synced to remote.
    workdir = config.get('workdir')

    # The setup command.  Will be run under the working directory.
    setup = config['setup']['command']

    # The command to run.  Will be run under the working directory.
    run = config['run']['command']

    ### Optional: download data to VM's local disks. ###
    # Format: {VM paths: local paths / cloud URLs}.
    file_mounts = config.get('file_mounts')
    if file_mounts is not None:
        file_mounts = dict(file_mounts)

        for src, dst in file_mounts.items():
            run = run.replace(src, dst)

    ### Optional end ###

    train = sky.Task(
        config['name'],
        workdir=workdir,
        setup=setup,
        run=run,
    )

    if file_mounts is not None:
        train.set_file_mounts(file_mounts)

    inputs = config['input']
    outputs = config['output']

    # TODO: allow option to say (or detect) no download/egress cost.
    train.set_inputs(**inputs)
    train.set_outputs(**outputs)

    resources = config['resources']
    if resources.get('cloud') is not None:
        resources['cloud'] = CLOUD_REGISTRY[resources['cloud']]
    if resources.get('accelerators') is not None:
        resources['accelerators'] = dict(resources['accelerators'])
    resources = sky.Resources(**resources)

    train.set_resources({
        resources,

        ##### Fully specified
        # sky.Resources(clouds.GCP(), 'n1-standard-16'),
        # sky.Resources(
        #     clouds.GCP(),
        #     'n1-standard-8',
        #     # Options: 'V100', {'V100': <num>}.
        #     'V100',
        # ),
        ##### Partially specified
        # sky.Resources(accelerators='V100'),
        # sky.Resources(accelerators='tpu-v3-8'),
        # sky.Resources(clouds.AWS(), accelerators={'V100': 4}),
        # sky.Resources(clouds.AWS(), accelerators='V100'),
    })

    # Optionally, specify a time estimator: Resources -> time in seconds.
    # train.set_time_estimator(time_estimators.resnet50_estimate_runtime)

dag = sky.optimize(dag, minimize=sky.Optimizer.COST)
# sky.execute(dag, dryrun=True)
sky.execute(dag)
