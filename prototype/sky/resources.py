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
            tf_version: str = None,
            tpu_name: str = None,
            accelerator_args: Dict[str, str] = None,
    ):
        self.cloud = cloud
        self.instance_type = instance_type
        assert not (instance_type is not None and cloud is None), \
            'If instance_type is specified, must specify the cloud'
        if accelerators is not None and type(accelerators) is str:
            if 'tpu' in accelerators:
                assert accelerator_args is not None, 'accelerator_args must be specified together with TPU'
                assert 'tf_version' in accelerator_args, 'missing tf_version in accelerator_args'
                assert 'tpu_name' in accelerator_args, 'missing tpu_name in accelerator_args'
            accelerators = {accelerators: 1}
        self.accelerators = accelerators
        self.tf_version = tf_version
        self.tpu_name = tpu_name
        self.accelerator_args = accelerator_args

    def __repr__(self) -> str:
        if self.accelerators is not None:
            return f'{self.cloud}({self.instance_type}, {self.accelerators})'
        return f'{self.cloud}({self.instance_type})'

    def is_launchable(self) -> bool:
        return self.cloud is not None and self.instance_type is not None

    def get_accelerators(self) -> Optional[Dict[str, int]]:
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
