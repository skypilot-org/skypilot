import sky
from sky import clouds


##############################
# Options for inputs:
#
#  P0:
#    - read from the specified cloud store, continuously
#    - sync submission-site local files to remote FS
#
#  1. egress from the specified cloud store, to local
#
#  TODO: if egress, what bucket to use for the destination cloud store?
#
##############################
# Options for outputs:
#
#  P0. write to local only (don't destroy VM at the end)
#
#  P1. write to local, sync to a specified cloud's bucket
#
#  P2. continuously write checkpoints to a specified cloud's bucket
#       TODO: this is data egress from run_cloud; not taken into account

with sky.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    workdir = '/Users/weichiang/Workspace/research/sky-experiments/prototype/codes/'

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
    train.set_inputs('gs://cloud-tpu-test-datasets/fake_imagenet',
                     estimated_size_gigabytes=70)
    train.set_outputs('resnet-model-dir', estimated_size_gigabytes=0.1)
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
        sky.Resources(accelerators='tpu-v3-8', tf_version='2.5.0', tpu_name='weilin-bert-test-big'),
        # sky.Resources(clouds.AWS(), accelerators={'V100': 4}),
        # sky.Resources(clouds.AWS(), accelerators='V100'),
    })

    # Optionally, specify a time estimator: Resources -> time in seconds.
    # train.set_time_estimator(time_estimators.resnet50_estimate_runtime)

dag = sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
# sky.execute(dag, dryrun=True)
sky.execute(dag)
