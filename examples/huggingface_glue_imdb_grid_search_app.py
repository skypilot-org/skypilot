"""Grid search version of huggingface_glue_imdb_app.py."""
import sky

resources_to_launch = sky.Resources(sky.AWS(), accelerators={'V100': 4})
with sky.Dag() as dag:
    # Setup command, run once (pip, download dataset).
    common_setup = """\
        git clone https://github.com/huggingface/transformers/
        cd transformers
        pip3 install .
        cd examples/pytorch/text-classification
        pip3 install -r requirements.txt
        python3 -c 'import datasets; datasets.load_dataset("imdb")'"""
    sky.Task(setup=common_setup).set_resources(resources_to_launch)
# `detach_run` will only detach the `run` command. The provision and `setup` are
# still blocking.
sky.launch(dag, cluster_name='hgs', detach_run=True)

for lr in [1e-5, 2e-5, 3e-5, 4e-5]:
    # To be filled in: {lr}.
    run_format = f"""\
        cd transformers/examples/pytorch/text-classification
        python3 run_glue.py
            --learning_rate {lr}
            --output_dir /tmp/imdb-{lr}/
            --model_name_or_path bert-base-cased
            --dataset_name imdb
            --do_train
            --max_seq_length 128
            --per_device_train_batch_size 32
            --max_steps 50
            --fp16 --overwrite_output_dir 2>&1 | tee run-{lr}.log'
        """

    per_trial_resources = sky.Resources(accelerators={'V100': 1})

    task = sky.Task(
        # A descriptive name.
        f'task-{lr}',
        # Run command for each task, with different lr.
        run=run_format.format(lr=lr)).set_resources(per_trial_resources)

    # Set 'stream_logs=False' to not mix all tasks' outputs together.
    # Each task's output is redirected to run-{lr}.log and can be tail-ed.
    sky.exec(task, cluster_name='hgs', stream_logs=False, detach_run=True)
