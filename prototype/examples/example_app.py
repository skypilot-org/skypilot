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
import sky

import time_estimators

file_mounts = {
    '~/application_default_credentials.json': '~/.config/gcloud/application_default_credentials.json',
    '~/.aws': '~/.aws',
}
setup = """
        echo "export GOOGLE_APPLICATION_CREDENTIALS=~/application_default_credentials.json" >> ~/.bashrc
        echo "export GOOGLE_CLOUD_PROJECT=intercloud-320520" >> ~/.bashrc
        """


def make_application():
    """A simple application: train_op -> infer_op."""

    with sky.Dag() as dag:
        # Train.
        train_op = sky.Task(
            'train_op',
            # run='python train.py --data_dir=INPUTS[0] --model_dir=OUTPUTS[0]')
            run="""\
                echo "Training on INPUTS[0]"; ls INPUTS[0]
                mkdir -p OUTPUTS[0]
                echo $(hostname) >> OUTPUTS[0]/model.pt; echo "Generated model in OUTPUTS[0]"
                """,
            setup=setup)

        train_op.set_inputs(
            # 's3://my-imagenet-data',
            's3://sky-example-test',
            estimated_size_gigabytes=150,
            # estimated_size_gigabytes=1500,
            # estimated_size_gigabytes=600,
        )

        # 'CLOUD': saves to the cloud this op ends up executing on.
        train_op.set_outputs('CLOUD://sky-my-model',
                             estimated_size_gigabytes=0.1)

        train_op.set_resources({
            sky.Resources(sky.AWS(), 'p3.2xlarge'),  # 1 V100, EC2.
            sky.Resources(sky.AWS(), 'p3.8xlarge'),  # 4 V100s, EC2.
            # Tuples mean all resources are required.
            sky.Resources(sky.GCP(), 'n1-standard-8', 'tpu-v3-8'),
        })

        train_op.set_file_mounts(file_mounts)

        train_op.set_time_estimator(time_estimators.resnet50_estimate_runtime)

        # Infer.
        infer_op = sky.Task(
            'infer_op',
            # run='python infer.py --model_dir=INPUTS[0]')
            run='echo "Infering on INPUTS[0]"; ls INPUTS[0]',
            setup=setup)

        # Data dependency.
        # FIXME: make the system know this is from train_op's outputs.
        infer_op.set_inputs(train_op.get_outputs(),
                            estimated_size_gigabytes=0.1)

        infer_op.set_resources({
            # TODO(zhwu): inf1 will cause autoscaler failure of ray during the placement_group fulfilling.
            sky.Resources(sky.AWS(), 'inf1.2xlarge', use_spot=True),
            sky.Resources(sky.AWS(), 'p3.2xlarge', use_spot=True),
            sky.Resources(sky.GCP(), 'n1-standard-4', 'T4', use_spot=True),
            sky.Resources(sky.GCP(), 'n1-standard-8', 'T4', use_spot=True),
        })

        infer_op.set_time_estimator(
            time_estimators.resnet50_infer_estimate_runtime)

        infer_op.set_file_mounts(file_mounts)

        # Chain the sky.tasks (Airflow syntax).
        # The dependency represents data flow.
        train_op >> infer_op

    return dag


dag = make_application()
# sky.launch_chain(dag, optimize_target=sky.OptimizeTarget.COST)
sky.launch_chain(dag, optimize_target=sky.OptimizeTarget.TIME)
