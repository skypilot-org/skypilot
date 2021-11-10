"""Grid search version of huggingface_glue_imdb_app.py."""
import sky
from sky import clouds

with sky.Dag() as dag:

    # Setup command, run once (pip, download dataset).
    common_setup = '\
      (git clone https://github.com/huggingface/transformers/ || true) && \
      cd transformers && pip3 install . && \
      cd examples/pytorch/text-classification && \
      pip3 install -r requirements.txt && \
      python3 -c \'import datasets; datasets.load_dataset("imdb")\''

    # To be filled in: {lr}.
    run_format = 'cd transformers/examples/pytorch/text-classification && \
        python3 run_glue.py \
            --learning_rate {lr} \
            --output_dir /tmp/imdb-{lr}/ \
            --model_name_or_path bert-base-cased \
            --dataset_name imdb  \
            --do_train \
            --max_seq_length 128 \
            --per_device_train_batch_size 32 \
            --num_train_epochs 1 \
            --fp16 2>&1 | tee run-{lr}.log'

    per_trial_resources = sky.Resources(accelerators={'V100': 1})
    resources_to_launch = sky.Resources(clouds.AWS(), accelerators={'V100': 4})

    tasks = []
    for lr in [1e-5, 2e-5, 3e-5, 4e-5]:
        task = sky.Task(
            # A descriptive name.
            f'task-{lr}',
            # All tasks share the same setup command (run once).
            setup=common_setup,
            # Run command for each task, with different lr.
            run=run_format.format(lr=lr)).set_resources(per_trial_resources)
        tasks.append(task)

    # Groups all tasks under a sky.ParTask.
    sky.ParTask(tasks).set_resources(resources_to_launch)

dag = sky.Optimizer.optimize(dag)

# Set 'stream_logs=False' to not mix all tasks' outputs together.
# Each task's output is redirected to run-{lr}.log and can be tail-ed.
sky.execute(dag, stream_logs=False)
