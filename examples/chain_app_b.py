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

# CLUSTER_NAME = 'test-chain-app-b'
CLUSTER_NAME = 'profile-azure'

# TODO (zhwu): add real opaque code
PROC_SETUP = """\
conda activate tf
if [ $? -eq 0 ]; then
    echo conda env exists
else
    conda create -n tf python=3.7 -y
    conda activate tf
    pip install tensorflow==2.5.1 tensorflow-datasets==4.4.0
    pip install protobuf==3.20
fi
"""

PROC_RUN = textwrap.dedent("""\
        conda activate tf
        python -u << EOF
        import tensorflow_datasets as tfds
        tfds.load('amazon_us_reviews/Books_v1_02',
                    split='train',
                    with_info=True,
                    download=True,
                    data_dir='OUTPUTS[0]')
        EOF
        echo Annonymizing dataset
        ls OUTPUTS[0]
        echo Done.
        """)

SETUP = """\
use_tpu=0
[ -z "$TPU_NAME" ] && echo 'export use_tpu=0' >> ~/.bashrc || echo 'export use_tpu=1' >> ~/.bashrc

use_gpu=0
nvidia-smi && (echo 'export use_gpu=1' >> ~/.bashrc) || echo 'export use_gpu=0' >> ~/.bashrc

use_inf=0
neuron-ls && (echo 'export use_inf=1' >> ~/.bashrc) || echo 'export use_inf=0' >> ~/.bashrc

source ~/.bashrc
. $(conda info --base)/etc/profile.d/conda.sh

pip install --upgrade pip

if [ "$use_inf" -eq 1 ]; then
    # Install OS headers
    sudo snap install linux-headers-$(uname -r) -y

    # Install Neuron Driver
    sudo apt-get install aws-neuron-dkms --allow-change-held-packages -y
    conda activate aws_neuron_tensorflow2_p37
    # Require to be 2.5.3 to compile correctly
    pip install tensorflow==2.5.3 tensorflow-datasets==4.4.0 transformers==4.12.0 tensorflow-text==2.5.0 tensorflow-neuron[cc]==2.5.3.* "protobuf<4" pandas
else
    pip install --upgrade pip
    conda activate huggingface || \
    (conda create -n huggingface python=3.7 -y && \
    conda activate huggingface && \
    pip install -r requirements.txt && \
    pip install protobuf==3.20 pandas)
fi
"""

TRAIN_SETUP = SETUP

TRAIN_RUN = """\
if [ "$use_gpu" -eq 1 ]; then
    conda activate huggingface
    num_gpus=`nvidia-smi --list-gpus | wc -l`
    export XLA_FLAGS='--xla_gpu_cuda_data_dir=/usr/local/cuda/' && \
    time python -u run_tpu.py \
    --tpu gpu \
    --data_dir INPUTS[0] \
    --model_dir OUTPUTS[0] \
    --per_core_batch_size 20 \
    --num_cores $num_gpus \
    --num_epochs 10 \
    --mode=train --amp --xla
fi
if [ "$use_tpu" -eq 1 ]; then
    conda activate huggingface
    python -u run_tpu.py \
    --tpu $TPU_NAME \
    --data_dir INPUTS[0] \
    --per_core_batch_size 32 \
    --model_dir OUTPUTS[0] \
    --num_epochs 10 \
    --mode=train | tee tpu.log
fi
"""

INFER_SETUP = SETUP
INFER_RUN = """\
mkdir -p ./saved_model
mv INPUTS[0]/* ./saved_model
BATCH_SIZE=8

if [ "$use_gpu" -eq 1 ]; then
    conda activate huggingface
    export XLA_FLAGS='--xla_gpu_cuda_data_dir=/usr/local/cuda/' && \
    python -u run_tpu.py \
    --tpu=gpu \
    --model_dir ./saved_model \
    --num_epochs 1 \
    --num_cores 1 \
    --per_core_batch_size $BATCH_SIZE \
    --mode=infer --amp --xla | tee gpu-inference.log
fi
if [ "$use_tpu" -eq 1 ]; then
    conda activate huggingface
    python -u run_tpu.py \
    --tpu $TPU_NAME \
    --model_dir ./saved_model \
    --num_epochs 1 \
    --per_core_batch_size $((BATCH_SIZE / 8)) \
    --mode=infer | tee tpu-inference.log
fi
if [ "$use_inf" -eq 1 ]; then
    conda activate aws_neuron_tensorflow2_p37
    python -u inferentia_compile.py
    python -u inferentia_inference.py
fi
"""


def make_application():
    """A simple application: train_op -> infer_op."""

    with sky.Dag() as dag:
        # Preprocess.
        # proc_op = sky.Task('proc_op', setup=PROC_SETUP, run=PROC_RUN)
        # # input can be downloaded with wget, so no input need to be set
        # proc_op.set_outputs('CLOUD://skypilot-pii-annonymized-dataset',
        #                     estimated_size_gigabytes=1)

        # proc_resources = {sky.Resources(sky.Azure(), 'Standard_DC8_v2')}
        # proc_op.set_resources(proc_resources)
        # proc_op.set_time_estimator(lambda _: 0.6)

        # Train.
        train_op = sky.Task(
            'train_op',
            setup=TRAIN_SETUP,
            run=TRAIN_RUN,
            workdir='./examples/tpu/tpu_app_code',
            inputs_outputs_on_bucket=True,
        )
        # inputs_outputs_on_bucket=True)

        train_op.set_inputs(
            'gs://skypilot-pii-annonymized-dataset',
            # proc_op.get_outputs(),
            estimated_size_gigabytes=1,
        )

        # 'CLOUD': saves to the cloud this op ends up executing on.
        train_op.set_outputs(
            'CLOUD://skypilot-pipeline-b-model',
            estimated_size_gigabytes=1)

        train_resources = {
            sky.Resources(sky.Azure(),
                          accelerators={'V100': 4}),  # 1 V100, EC2.
            # sky.Resources(sky.AWS(), 'p3.8xlarge'),  # 4 V100s, EC2.
            # sky.Resources(sky.GCP(), accelerators={'V100': 1}),  # 1 V100s, GCP.
            # sky.Resources(sky.GCP(), accelerators={'V100': 4}),  # 4 V100s, GCP.
            # Tuples mean all resources are required.
            # sky.Resources(sky.GCP(), 'n1-standard-8', 'tpu-v3-8'),
        }
        train_op.set_resources(train_resources)

        # TODO(zhwu): time estimator for BERT
        train_op.set_time_estimator(
            time_estimators.bert_base_finetune_estimate_runtime)

        # Infer.
        infer_op = sky.Task('infer_op',
                            setup=INFER_SETUP,
                            run=INFER_RUN,
                            workdir='./examples/tpu/tpu_app_code')

        # Data dependency.
        # FIXME: make the system know this is from train_op's outputs.
        infer_op.set_inputs(train_op.get_outputs(),
                            estimated_size_gigabytes=0.1)

        # NOTE(zhwu): Have to add use_spot here, since I only have spot quota
        # for inf instances
        infer_op.set_resources({
            # sky.Resources(sky.AWS(), 'inf1.2xlarge', use_spot=True),
            # sky.Resources(sky.AWS(), 'p3.2xlarge', use_spot=True),
            # sky.Resources(sky.GCP(), 'n1-standard-8', 'T4', use_spot=True),
            sky.Resources(sky.Azure(), accelerators={'T4': 1})
        })

        # TODO(zhwu): time estimator for BERT
        infer_op.set_time_estimator(
            time_estimators.bert_base_infer_estimate_runtime)

        # # Chain the tasks (Airflow syntax).
        # # The dependency represents data flow.
        # proc_op >> train_op
        train_op >> infer_op

    return dag


dag = make_application()
sky.execution._launch_chain(dag,
                            cluster_name=CLUSTER_NAME,
                            retry_until_up=True,
                            dryrun=False)
