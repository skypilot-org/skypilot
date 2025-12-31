import sky

with sky.Dag() as dag:
    # The working directory contains all code and will be synced to remote.
    workdir = './examples/tpu/tpu_app_code'

    # The setup command.  Will be run under the working directory.
    setup = 'source ~/huggingface/bin/activate || \
          (uv venv ~/huggingface --python 3.8 --seed && \
           source ~/huggingface/bin/activate && \
           uv pip install -r requirements.txt)'

    # The command to run.  Will be run under the working directory.
    run = 'source ~/huggingface/bin/activate && python -u run_tpu.py'

    train = sky.Task(
        'train',
        workdir=workdir,
        setup=setup,
        run=run,
    )
    train.set_resources({
        sky.Resources(accelerators='tpu-v3-8',
                      accelerator_args={
                          'runtime_version': '2.12.0',
                          'tpu_name': 'weilin-bert-test-big'
                      }),
    })

sky.launch(dag)
