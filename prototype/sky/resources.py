from typing import Dict, Optional, Union

from sky import clouds
from sky import logging

logger = logging.init_logger(__name__)


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
            accelerator_args: Dict[str, str] = {},
    ):
        self.cloud = cloud
        self.instance_type = instance_type
        assert not (instance_type is not None and cloud is None), \
            'If instance_type is specified, must specify the cloud'
        # Convert to Dict[str, int].
        if accelerators is not None and type(accelerators) is str:
            if 'tpu' in accelerators:
                assert accelerator_args is not None, \
                    'accelerator_args must be specified together with TPU'
                if 'tf_version' not in accelerator_args:
                    logger.info('Missing tf_version in accelerator_args, using'
                                ' default (2.5.0)')
                    accelerator_args['tf_version'] = '2.5.0'
                if 'tpu_name' not in accelerator_args:
                    logger.info('Missing tpu_name in accelerator_args, using'
                                ' default (sky_tpu)')
                    accelerator_args['tpu_name'] = 'sky_tpu'
            accelerators = {accelerators: 1}
        self.accelerators = accelerators
        self.accelerator_args = accelerator_args

    def __repr__(self) -> str:
        accelerators = ''
        accelerator_args = ''
        if self.accelerators is not None:
            accelerators = f', {self.accelerators}'
            if self.accelerator_args:
                accelerator_args = f', accelerator_args={self.accelerator_args}'
        return f'{self.cloud}({self.instance_type}{accelerators}{accelerator_args})'

    def is_launchable(self) -> bool:
        return self.cloud is not None and self.instance_type is not None

    def get_accelerators(self) -> Optional[Dict[str, int]]:
        """Returns the accelerators field directly or by inferring.

        For example, Resources(AWS, 'p3.2xlarge') has its accelerators field
        set to None, but this function will infer {'V100': 1} from the instance
        type.
        """
        if self.accelerators is not None:
            return self.accelerators
        if self.cloud is not None and self.instance_type is not None:
            return self.cloud.get_accelerators_from_instance_type(
                self.instance_type)
        return None

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
