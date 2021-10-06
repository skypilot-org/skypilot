from sky import clouds


class GCP(clouds.Cloud):

    _REPR = 'GCP'

    # In general, query this from the cloud.
    # NOTE: assumes us-central1.
    _ON_DEMAND_PRICES = {
        # GPUs: https://cloud.google.com/compute/gpus-pricing.
        # V100
        '1x V100': 2.48,
        '2x V100': 2.48 * 2,
        '4x V100': 2.48 * 4,
        '8x V100': 2.48 * 8,
        # T4
        '1x T4': 0.35,
        '2x T4': 0.35 * 2,
        '4x T4': 0.35 * 4,
        # TPUs: https://cloud.google.com/tpu/pricing.
        'tpu-v2-8': 4.5,
        'tpu-v3-8': 8.0,
        # VMs: https://cloud.google.com/compute/all-pricing.
        'n1-standard-4': 0.189999,
        'n1-standard-8': 0.379998,
    }

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
