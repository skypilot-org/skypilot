from typing import Dict, Optional, Union

from sky import clouds


class Resources(object):
    """A cloud resource bundle.

    Used
      * for representing resource requests for tasks/apps
      * as a "filter" to get concrete launchable instances
      * for calculating billing
      * for provisioning on a cloud

    Examples:

        # Fully specified cloud and instance type (is_launchable() is True).
        sky.Resources(clouds.AWS(), 'p3.2xlarge'),
        sky.Resources(clouds.GCP(), 'n1-standard-16'),
        sky.Resources(clouds.GCP(), 'n1-standard-8', 'V100')

        # Specifying required resources; Sky decides the cloud/instance type.
        sky.Resources(accelerators='V100'),
        sky.Resources(clouds.GCP(), accelerators={'V100': 1}),

        # TODO:
        sky.Resources(requests={'mem': '16g', 'cpu': 8})
    """

    def __init__(
            self,
            cloud: Optional[clouds.Cloud] = None,
            instance_type: Optional[str] = None,
            accelerators: Union[None, str, Dict[str, int]] = None,
    ):
        self.cloud = cloud
        self.instance_type = instance_type
        if accelerators is not None and type(accelerators) is str:
            accelerators = {accelerators: 1}
        self.accelerators = accelerators

    def __repr__(self) -> str:
        if self.accelerators is not None:
            return f'{self.cloud}({self.instance_type}, {self.accelerators})'
        return f'{self.cloud}({self.instance_type})'

    def is_launchable(self) -> bool:
        return self.cloud is not None and self.instance_type is not None

    def get_accelerators(self) -> Union[None, Dict[str, int]]:
        return self.accelerators

    def get_cost(self, seconds):
        """Returns cost in USD for the runtime in seconds."""
        hours = seconds / 3600
        # Instance.
        hourly_cost = self.cloud.instance_type_to_hourly_cost(
            self.instance_type)
        # Accelerators (if any).
        if self.accelerators is not None:
            hourly_cost += self.cloud.accelerators_to_hourly_cost(
                self.accelerators)
        return hourly_cost * hours
