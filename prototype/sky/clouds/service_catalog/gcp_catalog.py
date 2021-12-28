"""GCP Offerings Catalog.

For now this service catalog is manually coded. In the future it should be
queried from GCP API.
"""

from typing import Dict, List, Optional

from sky.clouds.service_catalog import common

_df = common.read_catalog('gcp.csv')


def list_accelerators(gpus_only: bool,
                      name_filter: Optional[str]) -> Dict[str, List[int]]:
    """Returns TODO.
    """
    return common.list_accelerators_impl('GCP', _df, gpus_only, name_filter)
