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
# CLUSTER_NAME = 'chain-profile-gpu'

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

            git clone https://github.com/Michaelvll/skypilot-ml-pipeline-exp.git ./codebase || true
            cd codebase
            conda activate resnet

            if [ $? -eq 0 ]; then
                echo "conda env exists"
            else
                if [ "$use_gpu" -eq 1 ]; then
                    conda create -n resnet python=3.7 -y
                    conda activate resnet
                    conda install cudatoolkit=11.0 -y
                    pip install pyyaml
                    pip install tensorflow==2.5.0 pyyaml pillow pandas
                    pip install protobuf==3.20
                fi
                if [ "$use_tpu" -eq 1 ]; then
                    conda create -n resnet python=3.7 -y
                    conda activate resnet
                    pip install tensorflow==2.5.0 pyyaml cloud-tpu-client pillow pandas
                    pip install protobuf==3.20
                fi
                if [ "$use_inf" -eq 1 ]; then
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
        num_gpus=`nvidia-smi --list-gpus | wc -l`
        export XLA_FLAGS='--xla_gpu_cuda_data_dir=/usr/local/cuda/' && \\
        time python3 -u resnet50_tpu/resnet50.py \\
        --tpu=gpu \\
        --data=INPUTS[0] \\
        --precision=float16 \\
        --model_dir=OUTPUTS[0]/resnet-realImagenet-float16 \\
        --num_cores=$num_gpus \\
        --per_core_batch_size=256 \\
        --amp --xla --loss_scale=128 \\
        2>&1 | tee run-realData-gpu-float16.log
    fi
    if [ "$use_tpu" -eq 1 ]; then
        time python3 -u resnet50_tpu/resnet50.py \\
          --tpu=$TPU_NAME \\
          --data=INPUTS[0] \\
          --precision=bfloat16 \\
          --model_dir=OUTPUTS[0]/resnet-realImagenet-float16 \\
          2>&1 | tee run-realData-float16.log
    fi
    if [ "$use_inf" -eq 1 ]; then
        exit 1
    fi
    """) if REAL_TRAIN else "echo 'MOCK TRAINING' | tee OUTPUTS[0]/model.pt"

INFER_SETUP = SETUP if REAL_TEST else "echo 'MOCK INFER_SETUP'"

INFER_RUN = textwrap.dedent(f"""\
                cd codebase

                conda activate resnet
                
                if [ "$use_gpu" -eq 1 ]; then
                    cd resnet50_tpu
                    export XLA_FLAGS='--xla_gpu_cuda_data_dir=/usr/local/cuda/' && \
                    OMP_NUM_THREADS=8 python3 -u resnet50.py \
                        --tpu=gpu \
                        --mode=infer \
                        --precision=float16 \
                        --model_dir=$HOME/model \
                        --num_cores=1 \
                        --infer_images=1000000 \
                        --per_core_batch_size=16 \
                        --amp --xla --loss_scale=128 \
                        2>&1 | tee run-realData-gpu-float16.log
                fi
                if [ "$use_tpu" -eq 1 ]; then
                    cd resnet50_tpu
                    OMP_NUM_THREADS=8 python3 -u resnet50.py \
                        --mode=infer \
                        --num_cores=8 \
                        --per_core_batch_size=16 \
                        --precision=bfloat16 \
                        --tpu=$TPU_NAME \
                        --model_dir=$HOME/model \
                        2>&1 | tee run-realData-float16.log
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
        train_op = sky.Task('train_op',
                            setup=TRAIN_SETUP,
                            run=TRAIN_RUN,
                            inputs_outputs_on_bucket=True)

        train_op.set_inputs(
            # 'gs://test-chain-app-0-train-op-inputs-0',
            's3://sky-imagenet-bucket' if REAL_TRAIN else 's3://sky-example-test',
            estimated_size_gigabytes=150,
            # estimated_size_gigabytes=1500,
            # estimated_size_gigabytes=600,
        )

        # 'CLOUD': saves to the cloud this op ends up executing on.
        train_op.set_outputs(f'CLOUD://skypilot-pipeline-model-{CLUSTER_NAME}',
                             estimated_size_gigabytes=0.1)

        train_resources = {
            # sky.Resources(sky.AWS(), 'p3.2xlarge',
            #               disk_size=400),  # 1 V100, EC2.
            sky.Resources(sky.AWS(), 'p3.8xlarge',
                          disk_size=400),  # 4 V100s, EC2.
            # sky.Resources(sky.GCP(), accelerators={'V100': 1},
            #               disk_size=400),  # 1 V100s, GCP.
            # sky.Resources(sky.GCP(), accelerators={'V100': 4},
            #               disk_size=400),  # 4 V100s, GCP.
            # # Tuples mean all resources are required.
            # sky.Resources(sky.GCP(), 'n1-standard-8', 'tpu-v3-8',
            #               disk_size=400),
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
