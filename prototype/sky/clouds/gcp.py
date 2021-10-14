from sky import clouds


class GCP(clouds.Cloud):

    _REPR = 'GCP'

    # Pricing.  All info assumes us-central1.
    # In general, query pricing from the cloud.

    # GPUs: https://cloud.google.com/compute/gpus-pricing.
    _ON_DEMAND_PRICES_GPUS = {
        # T4
        'T4': 0.35,
        '1x T4': 0.35,
        '2x T4': 0.35 * 2,
        '4x T4': 0.35 * 4,
        # P4
        'P4': 0.60,
        '1x P4': 0.60,
        '2x P4': 0.60 * 2,
        '4x P4': 0.60 * 4,
        # V100
        'V100': 2.48,
        '1x V100': 2.48,
        '2x V100': 2.48 * 2,
        '4x V100': 2.48 * 4,
        '8x V100': 2.48 * 8,
        # P100
        'P100': 1.46,
        '1x P100': 1.46,
        '2x P100': 1.46 * 2,
        '4x P100': 1.46 * 4,
        # K80
        'K80': 0.45,
        '1x K80': 0.45,
        '2x K80': 0.45 * 2,
        '4x K80': 0.45 * 4,
        '8x K80': 0.45 * 8,
    }

    _ON_DEMAND_PRICES = dict(
        {
            # VMs: https://cloud.google.com/compute/all-pricing.
            'n1-standard-1': 0.04749975,
            'n1-standard-2': 0.0949995,
            'n1-standard-4': 0.189999,
            'n1-standard-8': 0.379998,
            'n1-standard-16': 0.759996,
            'n1-standard-32': 1.519992,
            'n1-standard-64': 3.039984,
            'n1-standard-96': 4.559976,
            # TPUs: https://cloud.google.com/tpu/pricing.
            'tpu-v2-8': 4.5,
            'tpu-v3-8': 8.0,
        },
        **_ON_DEMAND_PRICES_GPUS)

    def instance_type_to_hourly_cost(self, instance_type):
        return GCP._ON_DEMAND_PRICES[instance_type]

    def get_egress_cost(self, num_gigabytes):
        # In general, query this from the cloud:
        #   https://cloud.google.com/storage/pricing#network-pricing
        # NOTE: egress to worldwide (excl. China, Australia).
        if num_gigabytes <= 1024:
            return 0.12 * num_gigabytes
        elif num_gigabytes <= 1024 * 10:
            return 0.11 * num_gigabytes
        else:
            return 0.08 * num_gigabytes

    def __repr__(self):
        return GCP._REPR

    def is_same_cloud(self, other):
        return isinstance(other, GCP)

    def make_deploy_resources_variables(self, task):
        typs = task.best_resources.types
        if type(typs) is str:
            typs = [typs]
        else:
            typs = list(typs)
        # Find GPU spec, if any.
        gpu = None
        gpu_count = 0
        pos = None
        for i, typ in enumerate(typs):
            if typ in GCP._ON_DEMAND_PRICES_GPUS:
                assert gpu is None, 'GPU doubly specified? {}'.format(
                    task.best_resources)
                splits = typ.split('x ')
                gpu = splits[-1]
                gpu_count = 1 if len(splits) == 1 else int(splits[0])
                pos = i
        if gpu:
            # Convert to GCP spec.
            # https://cloud.google.com/compute/docs/gpus
            gpu = 'nvidia-tesla-{}'.format(gpu.lower())
            # Keep the instance type only.
            del typs[pos]
        assert len(typs) == 1, 'Ambiguous instance type: {}'.format(typs)
        return {
            'instance_type': typs[0],
            'gpu': gpu,
            'gpu_count': gpu_count,
        }
