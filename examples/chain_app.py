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

REAL_TRAIN = True
REAL_TEST = True

CLUSTER_NAME = 'test-chain-app'

SETUP = textwrap.dedent("""\
            use_tpu=0
            [ -z "$TPU_NAME" ] && echo 'export use_tpu=0' >> ~/.bashrc || echo 'export use_tpu=1' >> ~/.bashrc

            use_gpu=0
            nvidia-smi && (echo 'export use_gpu=1' >> ~/.bashrc) || echo 'export use_gpu=0' >> ~/.bashrc

            use_inf=0
            neuron-ls && (echo 'export use_inf=1' >> ~/.bashrc) || echo 'export use_inf=0' >> ~/.bashrc

            source ~/.bashrc
            . $(conda info --base)/etc/profile.d/conda.sh

            pip install --upgrade pip

            conda activate resnet

            if [ $? -eq 0 ]; then
                echo "conda env exists"
            else
                if [ "$use_gpu" -eq 1 ]; then
                    git clone https://github.com/concretevitamin/tpu ./codebase || true
                    cd ./codebase
                    git checkout 9459fee

                    conda create -n resnet python=3.7 -y
                    conda activate resnet
                    conda install cudatoolkit=11.0 -y
                    pip install pyyaml
                    pip install tensorflow==2.5.0 pyyaml
                    pip install protobuf==3.20
                fi
                if [ "$use_tpu" -eq 1 ]; then
                    git clone https://github.com/Michaelvll/skypilot-ml-pipeline-exp.git ./codebase || true
                    conda create -n resnet python=3.7 -y
                    conda activate resnet
                    pip install tensorflow==2.5.0 pyyaml cloud-tpu-client
                    pip install protobuf==3.20
                fi
                if [ "$use_inf" -eq 1 ]; then
                    git clone https://github.com/Michaelvll/skypilot-ml-pipeline-exp.git ./codebase || true

                    # Install OS headers
                    sudo snap install linux-headers-$(uname -r) -y

                    # Install Neuron Driver
                    sudo apt-get install aws-neuron-dkms --allow-change-held-packages -y
                    conda activate aws_neuron_tensorflow2_p37
                    pip install tensorflow==1.15.5 tensorflow-neuron==1.15.5.* neuron-cc "protobuf<4"
                fi
            fi
            """)

TRAIN_SETUP = SETUP if REAL_TRAIN else "echo 'MOCK TRAIN_SETUP'"

TRAIN_RUN = textwrap.dedent(f"""\
    cd codebase
    conda activate resnet
    
    if [ "$use_gpu" -eq 1 ]; then
        export XLA_FLAGS='--xla_gpu_cuda_data_dir=/usr/local/cuda/'
        python -u models/official/resnet/resnet_main.py --use_tpu=False \\
            --mode=train --train_batch_size=256 --train_steps=250 \\
            --iterations_per_loop=125 \\
            --data_dir=INPUTS[0]/ \\
            --model_dir=OUTPUTS[0]/resnet-realImagenet \\
            --export_dir=OUTPUTS[0]/resnet-savedmodel \\
            --amp --xla --loss_scale=128
    fi
    if [ "$use_tpu" -eq 1 ]; then
        python3 resnet50_tpu/resnet50.py \\
          --tpu=$TPU_NAME \\
          --data=INPUTS[0] \\
          --use_bfloat16=True \\
          --model_dir=OUTPUTS[0]/resnet-realImagenet-float16 \\
          --num_epochs=5 \\
          2>&1 | tee run-realData-float16.log
    fi
    if [ "$use_inf" -eq 1 ]; then
        exit 1
    fi
    """) if REAL_TRAIN else "echo 'MOCK TRAINING' | tee OUTPUTS[0]/model.pt"

INFER_SETUP = SETUP if REAL_TEST else "echo 'MOCK INFER_SETUP'"

INFER_RUN = textwrap.dedent(f"""\
                cd codebase

                if [ "$use_gpu" -eq 1 ]; then
                    conda activate resnet
                    python3 official/resnet/resnet_main.py \\
                        --mode=eval \\
                        --eval_batch_size=16 \\
                        --use_tpu=False \\
                        --data_dir=./kitten_small.jpg \\
                        --model_dir=INPUTS[0]/resnet-realImagenet \\
                        --train_batch_size=1024 \\
                        --iterations_per_loop=1251 \\
                        --train_steps=112590 2>&1 | tee run-realData-eval.log
                fi
                if [ "$use_tpu" -eq 1 ]; then
                    conda activate resnet
                    python3 official/resnet/resnet_main.py \\
                        --mode=eval \\
                        --eval_batch_size=16 \\
                        --tpu=$TPU_NAME \\
                        --data_dir=./kitten_small.jpg \\
                        --model_dir=INPUTS[0]/resnet-realImagenet-float16/saved_model \\
                        --train_batch_size=1024 \\
                        --iterations_per_loop=1251 \\
                        --train_steps=112590 2>&1 | tee run-realData-eval.log
                fi
                if [ "$use_inf" -eq 1 ]; then
                    cd inferentia
                    conda activate aws_neuron_tensorflow2_p37
                    mv INPUTS[0]/resnet-realImagenet-float16/saved_weights.h5 ./saved_weights.h5

                    python compile.py
                    python inference.py
                fi
                """)


def make_application():
    """A simple application: train_op -> infer_op."""

    with sky.Dag() as dag:
        # Train.
        train_op = sky.Task('train_op', setup=TRAIN_SETUP, run=TRAIN_RUN)

        train_op.set_inputs(
            's3://imagenet-bucket' if REAL_TRAIN else 's3://sky-example-test',
            estimated_size_gigabytes=150,
            # estimated_size_gigabytes=1500,
            # estimated_size_gigabytes=600,
        )

        # 'CLOUD': saves to the cloud this op ends up executing on.
        train_op.set_outputs('CLOUD://skypilot-pipeline-model',
                             estimated_size_gigabytes=0.1)

        train_resources = {
            sky.Resources(sky.AWS(), 'p3.2xlarge',
                          disk_size=400),  # 1 V100, EC2.
            sky.Resources(sky.AWS(), 'p3.8xlarge',
                          disk_size=400),  # 4 V100s, EC2.
            # Tuples mean all resources are required.
            sky.Resources(sky.GCP(), 'n1-standard-8', 'tpu-v3-8',
                          disk_size=400),
        }
        if not REAL_TRAIN:
            train_resources.add(sky.Resources(sky.GCP(), disk_size=400))
        train_op.set_resources(train_resources)

        train_op.set_time_estimator(time_estimators.resnet50_estimate_runtime)

        # Infer.
        infer_op = sky.Task('infer_op', setup=INFER_SETUP, run=INFER_RUN)

        # Data dependency.
        # FIXME: make the system know this is from train_op's outputs.
        infer_op.set_inputs(train_op.get_outputs(),
                            estimated_size_gigabytes=0.1)

        # NOTE(zhwu): Have to add use_spot here, since I only have spot quota
        # for inf instances
        infer_op.set_resources({
            sky.Resources(sky.AWS(), 'inf1.2xlarge', use_spot=True),
            sky.Resources(sky.AWS(), 'p3.2xlarge', use_spot=True),
            sky.Resources(sky.GCP(), 'n1-standard-4', 'T4', use_spot=True),
            sky.Resources(sky.GCP(), 'n1-standard-8', 'T4', use_spot=True),
        })

        infer_op.set_time_estimator(
            time_estimators.resnet50_infer_estimate_runtime)

        # # Chain the tasks (Airflow syntax).
        # # The dependency represents data flow.
        train_op >> infer_op

    return dag


dag = make_application()
sky.execution._launch_chain(dag,
                            cluster_name=CLUSTER_NAME,
                            retry_until_up=True,
                            dryrun=True)
