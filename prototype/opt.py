"""Assumptions:

- Users supply a estimate_runtime_func() for all nodes in the DAG.
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
from sky import clouds


def resnet50_estimate_runtime(resources):
    """A simple runtime model for Resnet50."""
    # 3.8 G Multiply-Adds, 2 FLOPs per MADD, 3 for fwd+bwd.
    flops_for_one_image = 3.8 * (10**9) * 2 * 3

    if isinstance(resources.cloud, clouds.AWS):
        instance = resources.types
        if instance == 'p3.2xlarge':
            num_v100s = 1
        elif instance == 'p3.8xlarge':
            num_v100s = 4
        elif instance == 'p3.16xlarge':
            num_v100s = 8
        else:
            assert False, 'Not supported: {}'.format(resources)

        # Adds communication overheads per step (in seconds).
        communication_slack = 0.0
        if num_v100s == 4:
            communication_slack = 0.15
        elif num_v100s == 8:
            communication_slack = 0.30

        max_per_device_batch_size = 256
        effective_batch_size = max_per_device_batch_size * num_v100s

        # 112590 steps, 1024 BS = 90 epochs.
        total_steps = 112590 * (1024.0 / effective_batch_size)
        flops_for_one_batch = flops_for_one_image * max_per_device_batch_size

        # 27 TFLOPs, harmonic mean b/t 15TFLOPs (single-precision) & 120 TFLOPs
        # (16 bit).
        utilized_flops = 27 * (10**12)
        print('****** trying 1/3 util for v100')
        utilized_flops = 120 * (10**12) / 3

        estimated_step_time_seconds = flops_for_one_batch / utilized_flops \
          + communication_slack
        estimated_run_time_seconds = estimated_step_time_seconds * total_steps

    elif isinstance(resources.cloud, clouds.GCP):
        assert 'tpu-v3-8' in resources.types, resources
        tpu_v3_8_flops = 420 * (10**12)
        known_resnet50_utilization = 0.445  # From actual profiling.

        # GPU - fixed to 1/3 util
        # TPU
        #  - 1/4 util: doesn't work
        #  - 1/3 util: works
        #  - 1/2 util: works
        print('*** trying hand written util for TPU')
        known_resnet50_utilization = 1 / 3

        max_per_device_batch_size = 1024
        total_steps = 112590  # 112590 steps, 1024 BS = 90 epochs.
        flops_for_one_batch = flops_for_one_image * max_per_device_batch_size
        utilized_flops = tpu_v3_8_flops * known_resnet50_utilization
        estimated_step_time_seconds = flops_for_one_batch / utilized_flops
        estimated_run_time_seconds = estimated_step_time_seconds * total_steps

        print('  tpu-v3-8 estimated_step_time_seconds',
              estimated_step_time_seconds)

    else:
        assert False, 'not supported cloud in prototype: {}'.format(
            resources.cloud)

    return estimated_run_time_seconds


def resnet50_infer_estimate_runtime(resources):
    # 3.8 G Multiply-Adds, 2 FLOPs per MADD.
    flops_for_one_image = 3.8 * (10**9) * 2
    num_images = 0.1 * 1e6  # TODO: vary this.
    num_images = 1e6  # TODO: vary this.
    num_images = 70 * 1e6  # TODO: vary this.

    instance = resources.types
    # assert instance in ['p3.2xlarge', 'inf1.2xlarge', 'nvidia-t4'], instance

    if instance == 'p3.2xlarge':
        # 120 TFLOPS TensorCore.
        print('****** trying 1/3 util for v100')
        utilized_flops = 120 * (10**12) / 3

        # # Max bs to keep p99 < 15ms.
        # max_per_device_batch_size = 8
        # max_per_device_batch_size = 8*1e3
        # max_per_device_batch_size = 1

        # num_v100s = 1
        # effective_batch_size = max_per_device_batch_size * num_v100s

        # # 112590 steps, 1024 BS = 90 epochs.
        # total_steps = num_images // effective_batch_size
        # flops_for_one_batch = flops_for_one_image * max_per_device_batch_size

        # estimated_step_time_seconds = flops_for_one_batch / utilized_flops
        # estimated_run_time_seconds = estimated_step_time_seconds * total_steps

        # TODO: this ignores offline vs. online.  It's a huge batch.
        estimated_run_time_seconds = \
            flops_for_one_image * num_images / utilized_flops
    elif instance == 'inf1.2xlarge':
        # Inferentia: 1 chip = 128T[F?]OPS
        # Each AWS Inferentia chip supports up to 128 TOPS (trillions of
        # operations per second) of performance [assume 16, as it casts to
        # bfloat16 by default).
        # TODO: also assume 1/3 utilization
        utilized_flops = 128 * (10**12) / 3
        # TODO: this ignores offline vs. online.  It's a huge batch.
        estimated_run_time_seconds = \
            flops_for_one_image * num_images / utilized_flops
    elif isinstance(instance, tuple) and instance[0] == '1x T4':
        # T4 GPU: 65 TFLOPS fp16
        utilized_flops = 65 * (10**12) / 3
        estimated_run_time_seconds = \
            flops_for_one_image * num_images / utilized_flops
    else:
        assert False, resources

    # print('** num images {} total flops {}'.format(
    #     num_images, flops_for_one_image * num_images))

    return estimated_run_time_seconds


def make_application():
    """A simple application: train_op -> infer_op."""

    with sky.Dag() as dag:
        # Train.
        train_op = sky.Task('train_op',
                            command='train.py',
                            args='--data_dir=INPUTS[0] --model_dir=OUTPUTS[0]')

        train_op.set_inputs(
            's3://my-imagenet-data',
            # estimated_size_gigabytes=150,
            # estimated_size_gigabytes=1500,
            estimated_size_gigabytes=600,
        )

        # 'CLOUD': saves to the cloud this op ends up executing on.
        train_op.set_outputs('CLOUD://my-model', estimated_size_gigabytes=0.1)

        train_op.set_allowed_resources({
            sky.Resources(clouds.AWS(), 'p3.2xlarge'),  # 1 V100, EC2.
            sky.Resources(clouds.AWS(), 'p3.8xlarge'),  # 4 V100s, EC2.
            # Tuples mean all resources are required.
            sky.Resources(clouds.GCP(), ('n1-standard-8', 'tpu-v3-8')),
        })

        train_op.set_estimate_runtime_func(resnet50_estimate_runtime)

        # Infer.
        infer_op = sky.Task('infer_op',
                            command='infer.py',
                            args='--model_dir=INPUTS[0]')

        # Data dependency.
        # FIXME: make the system know this is from train_op's outputs.
        infer_op.set_inputs(train_op.get_outputs(),
                            estimated_size_gigabytes=0.1)

        infer_op.set_allowed_resources({
            sky.Resources(clouds.AWS(), 'inf1.2xlarge'),
            sky.Resources(clouds.AWS(), 'p3.2xlarge'),
            sky.Resources(clouds.GCP(), ('1x T4', 'n1-standard-4')),
            sky.Resources(clouds.GCP(), ('1x T4', 'n1-standard-8')),
        })

        infer_op.set_estimate_runtime_func(resnet50_infer_estimate_runtime)

        # Chain the sky.tasks (Airflow syntax).
        # The dependency represents data flow.
        train_op >> infer_op

    return dag


dag = make_application()

sky.Optimizer.optimize(dag, minimize=sky.Optimizer.COST)
# sky.Optimizer.optimize(dag, minimize=Optimizer.TIME)
