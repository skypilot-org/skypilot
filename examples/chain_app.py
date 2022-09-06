"""Assumptions:

- Users supply a time estimator func for each node in the DAG.
- Assuming homogeneous instances:
  - Support for choosing # instances
- Support for heterogeneous instances?


Optimization problem:
 - min cost, subject to some constraints?
 - min latency, subject to some constraints?

DAG assumption: chain.  If multiple branches, take into account of parallelism?

Incorporate the notion of region/zone (affects pricing).
Incorporate the notion of per-account egress quota (affects pricing).
"""
import textwrap
import sky

import time_estimators

MOCK = False

TRAIN_SETUP = textwrap.dedent("""\
                git clone https://github.com/concretevitamin/tpu || true
                cd tpu
                nvidia-smi
                if [ $? -eq 0 ]; then
                    git checkout 9459fee
                fi

                pip install --upgrade pip

                conda activate resnet

                if [ $? -eq 0 ]; then
                    echo "conda env exists"
                else
                    conda create -n resnet python=3.7 -y
                    conda activate resnet
                    conda install cudatoolkit=11.0 -y
                    pip install tensorflow==2.4.0 pyyaml
                    pip install protobuf==3.20
                    cd models
                    pip install -e .
                fi
                """) if not MOCK else "echo 'MOCK TRAIN_SETUP'"

TRAIN_RUN = textwrap.dedent("""\
                cd tpu
                conda activate resnet

                export XLA_FLAGS='--xla_gpu_cuda_data_dir=/usr/local/cuda/'
                python -u models/official/resnet/resnet_main.py --use_tpu=False \
                    --mode=train --train_batch_size=256 --train_steps=250 \
                    --iterations_per_loop=125 \
                    --data_dir=INPUTS[0]/datasets/ILSVRC2012/imagenet/ \
                    --model_dir=OUTPUTS[0] \
                    --amp --xla --loss_scale=128
                """
) if not MOCK else "echo 'MOCK TRAINING' | tee OUTPUTS[0]/model.pt"


def make_application():
    """A simple application: train_op -> infer_op."""

    with sky.Dag() as dag:
        # Train.
        train_op = sky.Task('train_op', setup=TRAIN_SETUP, run=TRAIN_RUN)

        train_op.set_inputs(
            's3://imagenet-bucket' if not MOCK else 's3://sky-example-test',
            estimated_size_gigabytes=150,
            # estimated_size_gigabytes=1500,
            # estimated_size_gigabytes=600,
        )

        # 'CLOUD': saves to the cloud this op ends up executing on.
        train_op.set_outputs('CLOUD://skypilot-pipeline-model',
                             estimated_size_gigabytes=0.1)

        train_op.set_resources({
            # sky.Resources(sky.AWS(), 'p3.2xlarge',
            #               disk_size=400),  # 1 V100, EC2.
            # sky.Resources(sky.AWS(), 'p3.8xlarge',
            #               disk_size=400),  # 4 V100s, EC2.
            # Tuples mean all resources are required.
            sky.Resources(sky.GCP(), 'n1-standard-8', 'tpu-v3-8',
                          disk_size=400),
        })

        train_op.set_time_estimator(time_estimators.resnet50_estimate_runtime)

        # Infer.
        # infer_op = sky.Task('infer_op',
        #                     run='echo "Infering on INPUTS[0]"; ls INPUTS[0]')

        # # Data dependency.
        # # FIXME: make the system know this is from train_op's outputs.
        # infer_op.set_inputs(train_op.get_outputs(),
        #                     estimated_size_gigabytes=0.1)

        # infer_op.set_resources({
        #     sky.Resources(sky.AWS(), 'inf1.2xlarge', use_spot=True),
        #     sky.Resources(sky.AWS(), 'p3.2xlarge', use_spot=True),
        #     sky.Resources(sky.GCP(), 'n1-standard-4', 'T4', use_spot=True),
        #     sky.Resources(sky.GCP(), 'n1-standard-8', 'T4', use_spot=True),
        # })

        # infer_op.set_time_estimator(
        #     time_estimators.resnet50_infer_estimate_runtime)

        # # Chain the tasks (Airflow syntax).
        # # The dependency represents data flow.
        # train_op >> infer_op

    return dag


dag = make_application()
sky.launch(dag, cluster_name='test-chain-app')
