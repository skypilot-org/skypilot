import copy

from sky import clouds


class GCP(clouds.Cloud):

    _REPR = 'GCP'

    # Pricing.  All info assumes us-central1.
    # In general, query pricing from the cloud.
    _ON_DEMAND_PRICES = {
        # VMs: https://cloud.google.com/compute/all-pricing.
        # N1 standard
        'n1-standard-1': 0.04749975,
        'n1-standard-2': 0.0949995,
        'n1-standard-4': 0.189999,
        'n1-standard-8': 0.379998,
        'n1-standard-16': 0.759996,
        'n1-standard-32': 1.519992,
        'n1-standard-64': 3.039984,
        'n1-standard-96': 4.559976,
        # N1 highmem
        'n1-highmem-2': 0.118303,
        'n1-highmem-4': 0.236606,
        'n1-highmem-8': 0.473212,
        'n1-highmem-16': 0.946424,
        'n1-highmem-32': 1.892848,
        'n1-highmem-64': 3.785696,
        'n1-highmem-96': 5.678544,
    }
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
    # TPUs: https://cloud.google.com/tpu/pricing.
    _ON_DEMAND_PRICES_TPUS = {
        'tpu-v2-8': 4.5,
        'tpu-v3-8': 8.0,
    }
    _ON_DEMAND_PRICES.update(_ON_DEMAND_PRICES_GPUS)
    _ON_DEMAND_PRICES.update(_ON_DEMAND_PRICES_TPUS)

    def instance_type_to_hourly_cost(self, instance_type):
        return GCP._ON_DEMAND_PRICES[instance_type]

    def accelerators_to_hourly_cost(self, accelerators):
        assert len(accelerators) == 1, accelerators
        acc, acc_count = list(accelerators.items())[0]
        if acc in GCP._ON_DEMAND_PRICES_GPUS:
            # Assuming linear pricing.
            return GCP._ON_DEMAND_PRICES_GPUS[acc] * acc_count
        if acc in GCP._ON_DEMAND_PRICES_TPUS:
            assert acc_count == 1, accelerators
            return GCP._ON_DEMAND_PRICES_TPUS[acc]
        assert False, accelerators

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
        r = task.best_resources
        # Find GPU spec, if any.
        res_vars = {
            'instance_type': r.instance_type,
            'gpu': None,
            'gpu_count': None,
            'tpu': None}
        accelerators = r.get_accelerators()
        if accelerators is not None:
            assert len(accelerators) == 1, r
            acc, acc_count = list(accelerators.items())[0]
            if 'tpu' in acc:
                res_vars['tpu_type'] = acc.replace('tpu-', '')
                res_vars['tf_version'] = r.tf_version
                res_vars['tpu_name'] = r.tpu_name
            else:
                # Convert to GCP names: https://cloud.google.com/compute/docs/gpus
                res_vars['gpu'] = 'nvidia-tesla-{}'.format(acc.lower())
                res_vars['gpu_count'] = acc_count

        return res_vars

    @classmethod
    def get_default_instance_type(cls):
        return 'n1-highmem-8'

    def get_feasible_launchable_resources(self, resources):
        if resources.instance_type is not None:
            assert resources.is_launchable(), resources
            return [resources]
        # No other resources (cpu/mem) to filter for now, so just return a
        # default VM type.
        r = copy.deepcopy(resources)
        r.cloud = GCP()
        r.instance_type = GCP.get_default_instance_type()
        return [r]
