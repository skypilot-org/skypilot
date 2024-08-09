import apex

with apex.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    workdir = './examples/tpu/tpu_app_code'

    # The setup command.  Will be run under the working directory.
    setup = 'pip install --upgrade pip && \
        conda activate huggingface || \
          (conda create -n huggingface python=3.8 -y && \
           conda activate huggingface && \
           pip install -r requirements.txt)'

    # The command to run.  Will be run under the working directory.
    run = 'conda activate huggingface && python -u run_tpu.py'

    train = apex.Task(
        'train',
        workdir=workdir,
        setup=setup,
        run=run,
    )
    train.set_resources({
        apex.Resources(accelerators='tpu-v3-8',
                      accelerator_args={
                          'runtime_version': '2.12.0',
                          'tpu_name': 'weilin-bert-test-big'
                      }),
    })

apex.launch(dag)
