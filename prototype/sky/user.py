import os
import sqlite3
from typing import Dict, Optional, Union

from sky import clouds
from sky import logging

logger = logging.init_logger(__name__)


class Session(object):
    """User session manager.

    Used
      * for keeping track of user and job state

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

    def __init__(self):
        if not os.path.exists('.sky/session.db'):
          os.makedirs('.sky')
          self.db_conn = sqlite3.connect('.sky/session.db')
          self.init_session()

    def init_session(self):
        """Initializes the session database."""
        self.db_conn.execute(
            'CREATE TABLE IF NOT EXISTS session (key TEXT PRIMARY KEY, value TEXT)')
        self.db_conn.commit()

    def __repr__(self) -> str:
        accelerators = ''
        accelerator_args = ''
        if self.accelerators is not None:
            accelerators = f', {self.accelerators}'
            if self.accelerator_args:
                accelerator_args = f', accelerator_args={self.accelerator_args}'
        use_spot = ''
        if self.use_spot:
            use_spot = '[Spot]'
        return f'{self.cloud}({self.instance_type}{use_spot}{accelerators}{accelerator_args})'

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
            self.instance_type, self.use_spot)
        # Accelerators (if any).
        if self.accelerators is not None:
            hourly_cost += self.cloud.accelerators_to_hourly_cost(
                self.accelerators)
        return hourly_cost * hours
