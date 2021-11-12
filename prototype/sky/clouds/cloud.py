from typing import Dict, Optional


class Cloud(object):

    # TODO: incorporate region/zone into the API.
    def instance_type_to_hourly_cost(self, instance_type, use_spot):
        """Returns the hourly on-demand price for an instance type."""
        raise NotImplementedError

    def accelerators_to_hourly_cost(self, accelerators):
        """Returns the hourly on-demand price for accelerators."""
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

    def get_accelerators_from_instance_type(
            self,
            instance_type: str,
    ) -> Optional[Dict[str, int]]:
        """Returns {acc: acc_count} held by 'instance_type', if any."""
        raise NotImplementedError

    def get_default_instance_type(self):
        raise NotImplementedError

    def get_feasible_launchable_resources(self, resources):
        """Returns a list of feasible and launchable resources.

        Feasible resources refer to an offering respecting the resource
        requirements.  Currently, this function implements "filtering" the
        cloud's offerings only w.r.t. accelerators constraints.

        Launchable resources require a cloud and an instance type be assigned.
        """
        raise NotImplementedError
