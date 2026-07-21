import sky
from sky import sky_logging

logger = sky_logging.init_logger(__name__)

# Peak 16-bit throughput, in FLOPS.
V100_PEAK_FLOPS = 120 * (10**12)
T4_PEAK_FLOPS = 65 * (10**12)


def resnet50_estimate_runtime(resources):
    """A simple runtime model for Resnet50."""
    # 3.8 G Multiply-Adds, 2 FLOPs per MADD, 3 for fwd+bwd.
    flops_for_one_image = 3.8 * (10**9) * 2 * 3

    def _gpu(num_gpus, peak_flops):
        # Adds communication overheads per step (in seconds).
        communication_slack = 0.0
        if num_gpus == 4:
            communication_slack = 0.15
        elif num_gpus == 8:
            communication_slack = 0.30

        max_per_device_batch_size = 256
        effective_batch_size = max_per_device_batch_size * num_gpus

        # 112590 steps, 1024 BS = 90 epochs.
        total_steps = 112590 * (1024.0 / effective_batch_size)
        flops_for_one_batch = flops_for_one_image * max_per_device_batch_size

        # Assume the model sustains 1/3 of the device's peak throughput.
        utilized_flops = peak_flops / 3

        estimated_step_time_seconds = flops_for_one_batch / utilized_flops \
          + communication_slack
        estimated_run_time_seconds = estimated_step_time_seconds * total_steps
        return estimated_run_time_seconds

    if isinstance(resources.cloud, sky.AWS):
        instance = resources.instance_type
        if instance == 'g4dn.xlarge':
            num_t4s = 1
        elif instance == 'g4dn.12xlarge':
            num_t4s = 4
        elif instance == 'g4dn.metal':
            num_t4s = 8
        else:
            raise ValueError('Not supported: {}'.format(resources))
        return _gpu(num_t4s, T4_PEAK_FLOPS)

    elif isinstance(resources.cloud, sky.GCP):
        accelerators = resources.accelerators
        if accelerators is None:
            assert False, 'not supported'

        assert len(accelerators) == 1, resources
        for acc, acc_count in accelerators.items():
            break
        # GCP still offers V100s, unlike AWS.
        if acc == 'V100':
            assert acc_count in [1, 2, 4, 8], resources
            return _gpu(acc_count, V100_PEAK_FLOPS)

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
        logger.debug('  tpu-v3-8 estimated_step_time_seconds %f',
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

    if instance == 'inf1.2xlarge':
        # Inferentia: 1 chip = 128T[F?]OPS
        # Each AWS Inferentia chip supports up to 128 TOPS (trillions of
        # operations per second) of performance [assume 16, as it casts to
        # bfloat16 by default).
        # TODO: also assume 1/3 utilization
        utilized_flops = 128 * (10**12) / 3
        # TODO: this ignores offline vs. online.  It's a huge batch.
        estimated_run_time_seconds = \
            flops_for_one_image * num_images / utilized_flops
    elif resources.accelerators is not None:
        accs = resources.accelerators
        for acc, acc_count in accs.items():
            break
        assert acc == 'T4' and acc_count == 1, resources
        utilized_flops = T4_PEAK_FLOPS / 3
        estimated_run_time_seconds = \
            flops_for_one_image * num_images / utilized_flops
    else:
        assert False, resources

    # print('** num images {} total flops {}'.format(
    #     num_images, flops_for_one_image * num_images))

    return estimated_run_time_seconds
