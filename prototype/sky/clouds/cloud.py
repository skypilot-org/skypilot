class Cloud(object):

    # TODO: incorporate region/zone into the API.
    def instance_type_to_hourly_cost(self, instance_type):
        """Returns the hourly on-demand price for an instance type."""
        raise NotImplementedError

    def get_egress_cost(self, num_gigabytes):
        """Returns the egress cost.

        TODO: takes into account "per month" accumulation per account.
        """
        raise NotImplementedError

    def is_same_cloud(self, other):
        raise NotImplementedError
