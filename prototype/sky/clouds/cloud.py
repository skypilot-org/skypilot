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

    def make_deploy_resources_variables(self, task):
        """Converts a planned sky.Task into cloud-specific resource variables.

        These variables are used to fill the node type section (instance type,
        any accelerators, etc.) in the cloud's deployment YAML template.

        Cloud-agnostic sections (e.g., commands to run) need not be returned by
        this function.

        Returns:
          A dictionary of cloud-specific node type variables.
        """
        raise NotImplementedError
