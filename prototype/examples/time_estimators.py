from sky import clouds
import logging
logger = logging.getLogger(__name__)


def resnet50_estimate_runtime(resources):
    """A simple runtime model for Resnet50."""
    # 3.8 G Multiply-Adds, 2 FLOPs per MADD, 3 for fwd+bwd.
    flops_for_one_image = 3.8 * (10**9) * 2 * 3

    def _v100(num_v100s):
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

        # print('****** trying 1/3 util for v100')
        utilized_flops = 120 * (10**12) / 3

        estimated_step_time_seconds = flops_for_one_batch / utilized_flops \
          + communication_slack
        estimated_run_time_seconds = estimated_step_time_seconds * total_steps
        return estimated_run_time_seconds

    if isinstance(resources.cloud, clouds.AWS):
        instance = resources.instance_type
        if instance == 'p3.2xlarge':
            num_v100s = 1
        elif instance == 'p3.8xlarge':
            num_v100s = 4
        elif instance == 'p3.16xlarge':
            num_v100s = 8
        else:
            assert False, 'Not supported: {}'.format(resources)
        return _v100(num_v100s)

    elif isinstance(resources.cloud, clouds.GCP):
        accelerators = resources.get_accelerators()
        if accelerators is None:
            assert False, 'not supported'

        assert len(accelerators) == 1, resources
        for acc, acc_count in accelerators.items():
            break
        if acc == 'V100':
            assert acc_count in [1, 2, 4, 8], resources
            return _v100(acc_count)

        assert acc == 'tpu-v3-8', resources
        tpu_v3_8_flops = 420 * (10**12)
        known_resnet50_utilization = 0.445  # From actual profiling.

        # GPU - fixed to 1/3 util
        # TPU
        #  - 1/4 util: doesn't work
        #  - 1/3 util: works
        #  - 1/2 util: works

        # print('*** trying hand written util for TPU')
        known_resnet50_utilization = 1 / 3

        max_per_device_batch_size = 1024
        total_steps = 112590  # 112590 steps, 1024 BS = 90 epochs.
        flops_for_one_batch = flops_for_one_image * max_per_device_batch_size
        utilized_flops = tpu_v3_8_flops * known_resnet50_utilization
        estimated_step_time_seconds = flops_for_one_batch / utilized_flops
        estimated_run_time_seconds = estimated_step_time_seconds * total_steps
        logger.info('  tpu-v3-8 estimated_step_time_seconds',
                    estimated_step_time_seconds)
        return estimated_run_time_seconds

    else:
        assert False, 'not supported cloud in prototype: {}'.format(
            resources.cloud)


def resnet50_infer_estimate_runtime(resources):
    # 3.8 G Multiply-Adds, 2 FLOPs per MADD.
    flops_for_one_image = 3.8 * (10**9) * 2
    num_images = 0.1 * 1e6  # TODO: vary this.
    num_images = 1e6  # TODO: vary this.
    num_images = 70 * 1e6  # TODO: vary this.

    instance = resources.instance_type
    # assert instance in ['p3.2xlarge', 'inf1.2xlarge', 'nvidia-t4'], instance

    if instance == 'p3.2xlarge':
        # 120 TFLOPS TensorCore.
        logger.info('****** trying 1/3 util for v100')
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
    elif resources.get_accelerators() is not None:
        accs = resources.get_accelerators()
        for acc, acc_count in accs.items():
            break
        assert acc == 'T4' and acc_count == 1, resources
        # T4 GPU: 65 TFLOPS fp16
        utilized_flops = 65 * (10**12) / 3
        estimated_run_time_seconds = \
            flops_for_one_image * num_images / utilized_flops
    else:
        assert False, resources

    # print('** num images {} total flops {}'.format(
    #     num_images, flops_for_one_image * num_images))

    return estimated_run_time_seconds
