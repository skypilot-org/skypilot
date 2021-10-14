from sky import clouds


class AWS(clouds.Cloud):
    _REPR = 'AWS'

    # In general, query this from the cloud.
    _ON_DEMAND_PRICES = {
        # p3.
        'p3.2xlarge': 3.06,
        'p3.8xlarge': 12.24,
        'p3.16xlarge': 24.48,

        # inf1.
        'inf1.xlarge': 0.228,
        'inf1.2xlarge': 0.362,
        'inf1.6xlarge': 1.18,
        'inf1.24xlarge': 4.721,
    }

    def instance_type_to_hourly_cost(self, instance_type):
        return AWS._ON_DEMAND_PRICES[instance_type]

    def get_egress_cost(self, num_gigabytes):
        # In general, query this from the cloud:
        #   https://aws.amazon.com/s3/pricing/
        # NOTE: egress from US East (Ohio).
        if num_gigabytes > 150 * 1024:
            return 0.05 * num_gigabytes
        cost = 0.0
        if num_gigabytes >= 50 * 1024:
            cost += (num_gigabytes - 50 * 1024) * 0.07
            num_gigabytes -= 50 * 1024

        if num_gigabytes >= 10 * 1024:
            cost += (num_gigabytes - 10 * 1024) * 0.085
            num_gigabytes -= 10 * 1024

        if num_gigabytes > 1:
            cost += (num_gigabytes - 1) * 0.09

        cost += 0.0
        return cost

    def __repr__(self):
        return AWS._REPR

    def is_same_cloud(self, other):
        return isinstance(other, AWS)

    def make_deploy_resources_variables(self, task):
        return {'instance_type': task.best_resources.types}
