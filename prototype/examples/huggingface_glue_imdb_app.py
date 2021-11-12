"""Huggingface.

Fine-tunes a pretrained BERT model on the IMDB dataset:
https://github.com/huggingface/transformers/tree/master/examples/pytorch/text-classification

The dataset is downloaded automatically by huggingface, and saved to
~/.cache/huggingface.
"""
import sky

with sky.Dag() as dag:
    # The setup command.  Will be run under the working directory.
    # https://github.com/huggingface/transformers/tree/master/examples#important-note
    setup = '\
      (git clone https://github.com/huggingface/transformers/ || true) && \
      cd transformers && pip3 install . && \
      cd examples/pytorch/text-classification && \
      pip3 install -r requirements.txt'

    # The command to run.  Will be run under the working directory.
    # https://github.com/huggingface/transformers/tree/master/examples/pytorch/text-classification
    run = 'cd transformers/examples/pytorch/text-classification && \
    python3 run_glue.py \
  --model_name_or_path bert-base-cased \
  --dataset_name imdb  \
  --do_train \
  --do_predict \
  --max_seq_length 128 \
  --per_device_train_batch_size 32 \
  --learning_rate 2e-5 \
  --num_train_epochs 3 \
  --output_dir /tmp/imdb/ \
  --fp16'

    train = sky.Task(
        'train',
        setup=setup,
        run=run,
    )
    train.set_resources({sky.Resources(accelerators='K80')})

dag = sky.Optimizer.optimize(dag)
sky.execute(dag)
