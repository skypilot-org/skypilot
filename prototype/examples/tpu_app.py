import sky

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
    run = 'conda activate huggingface && python -u run_tpu.py'

    train = sky.Task(
        'train',
        workdir=workdir,
        setup=setup,
        run=run,
    )
    train.set_resources({
        sky.Resources(accelerators='tpu-v3-8',
                      accelerator_args={
                          'tf_version': '2.5.0',
                          'tpu_name': 'weilin-bert-test-big'
                      }),
    })

sky.launch(dag)
