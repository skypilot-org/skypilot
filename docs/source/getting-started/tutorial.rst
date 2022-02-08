Tutorial: DNN Training
======================

In this tutorial, we'll train a Transformer-based language model from HuggingFace.


Defining our Training Task
--------------------------

We'll start by specifying a Sky task YAML with our resource requirements, cluster setup script,
and run command:

.. code-block:: yaml

   # dnn.yaml

   name: huggingface

   resources:
      accelerators: V100

   setup: |
      git clone https://github.com/huggingface/transformers/
      cd transformers
      pip3 install .
      cd examples/pytorch/text-classification
      pip3 install -r requirements.txt

   run: |
      cd transformers/examples/pytorch/text-classification
      python3 run_glue.py \
         --model_name_or_path bert-base-cased \
         --dataset_name imdb  \
         --do_train \
         --max_seq_length 128 \
         --per_device_train_batch_size 32 \
         --learning_rate 2e-5 \
         --max_steps 50 \
         --output_dir /tmp/imdb/ --overwrite_output_dir \
         --fp16


We can launch training by running:

.. code-block:: console

   $ sky launch -c lm-cluster dnn.yaml

The above will kick off a single training run after provisioning a cluster. But
what if we would like to run multiple runs on the same cluster scheduled back-to-back
for common workflows such as hyperparameter tuning? This is where Sky's job queue steps in.

Scheduling Multiple Training Jobs
---------------------------------

TODO: show how to use job queue here