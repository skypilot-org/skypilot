"""Resource specification for SkyServe."""
import os
import textwrap
from typing import Any, Dict, Optional

import yaml

from sky.backends import backend_utils
from sky.utils import schemas
from sky.utils import ux_utils


class SkyResourcesSpec:
    """SkyServe resource specification."""

    def __init__(
        self,
        use_spot: bool = False,
        spot_recovery: Optional[str] = None,
    ):
        self._use_spot = use_spot
        self._spot_recovery = spot_recovery

    @staticmethod
    def from_yaml_config(config: Optional[Dict[str, Any]]):
        if config is None:
            return None

        backend_utils.validate_schema(config, schemas.get_resources_schema(),
                                      'Invalid resource YAML: ')

        resources_config = {}
        resources_config['use_spot'] = config.get('use_spot', False)
        resources_config['spot_recovery'] = config.get('spot_recovery', None)

        return SkyResourcesSpec(**resources_config)

    @staticmethod
    def from_yaml(yaml_path: str):
        with open(os.path.expanduser(yaml_path), 'r') as f:
            config = yaml.safe_load(f)

        if isinstance(config, str):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('YAML loaded as str, not as dict. '
                                 f'Is it correct? Path: {yaml_path}')

        if config is None:
            config = {}

        if 'resources' not in config:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'Resource YAML must have a "resources" section. '
                    f'Is it correct? Path: {yaml_path}')

        return SkyResourcesSpec.from_yaml_config(config['resources'])

    def __repr__(self) -> str:
        return textwrap.dedent(f"""\
            Use spot:        {self.use_spot}
            Spot recovery:   {self.spot_recovery}

            Please refer to SkyPilot Serve document for detailed explanations.
        """)

    @property
    def use_spot(self) -> bool:
        return self._use_spot

    @property
    def spot_recovery(self) -> Optional[str]:
        return self._spot_recovery
