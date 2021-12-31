"""GCP Offerings Catalog.

For now this service catalog is manually coded. In the future it should be
queried from GCP API.
"""

from typing import Dict, List, Optional

import pandas as pd

from sky.clouds.service_catalog import common

_df = common.read_catalog('gcp.csv')

_DEFAULT_REGION = 'us-central1'


def _get_accelerator(
        df: pd.DataFrame,
        accelerator: str,
        region: Optional[str],
) -> pd.DataFrame:
    idx = df['AcceleratorName'] == accelerator
    if region is not None:
        idx &= df['Region'] == region
    return df[idx]


def get_hourly_cost(accelerator: str,
                    region: str = _DEFAULT_REGION,
                    use_spot: bool = False) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    df = _get_accelerator(_df, accelerator, region)
    assert len(set(df['Price'])) == 1, df
    if not use_spot:
        return df['Price'].iloc[0]

    cheapest_idx = df['SpotPrice'].idxmin()
    if pd.isnull(cheapest_idx):
        return df['Price'].iloc[0]

    cheapest = df.loc[cheapest_idx]
    return cheapest['SpotPrice']


def list_accelerators(
        gpus_only: bool = False,
        name_filter: Optional[str] = None,
) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in GCP offering GPUs."""
    return common.list_accelerators_impl('GCP', _df, gpus_only, name_filter)
