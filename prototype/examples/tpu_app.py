import sky
from sky import clouds

with sky.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    workdir = './examples/tpu_app_codes'

    # The setup command.  Will be run under the working directory.
    setup = 'pip install --upgrade pip && \
        conda activate huggingface || \
          (conda create -n huggingface python=3.8 -y && \
           conda activate huggingface && \
           pip install -r requirements.txt)'

    # The command to run.  Will be run under the working directory.
    run = 'conda activate huggingface && \
        python -u run_tpu.py'

    train = sky.Task(
        'train',
        workdir=workdir,
        setup=setup,
        run=run,
    )
    train.set_resources({
        ##### Fully specified
        # sky.Resources(clouds.AWS(), 'p3.2xlarge'),
        # sky.Resources(clouds.GCP(), 'n1-standard-16'),
        # sky.Resources(
        #     clouds.GCP(),
        #     'n1-standard-8',
        #     # Options: 'V100', {'V100': <num>}.
        #     'V100',
        # ),
        ##### Partially specified
        #sky.Resources(accelerators='V100'),
        sky.Resources(accelerators='tpu-v3-8', accelerator_args={'tf_version': '2.5.0', 'tpu_name': 'weilin-bert-test-big'}),
        # sky.Resources(clouds.AWS(), accelerators={'V100': 4}),
        # sky.Resources(clouds.AWS(), accelerators='V100'),
    })

    # Optionally, specify a time estimator: Resources -> time in seconds.
    # train.set_time_estimator(time_estimators.resnet50_estimate_runtime)

dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
# sky.execute(dag, dryrun=True)
sky.execute(dag)
