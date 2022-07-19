"""A script that generates Google Cloud GPU catalog.

Google Cloud does not have an API for querying TPU/GPU offerings, so this
part is currently hard-coded.

TODO: Add support for regular VMs
https://cloud.google.com/sdk/gcloud/reference/compute/machine-types/list
"""

import numpy as np
import pandas as pd


_REGION_TO_ZONES = {
    'us-west1': ['us-west1-a', 'us-west1-b'],
    'us-central1': ['us-central1-a', 'us-central1-b', 'us-central1-c', 'us-central1-f'],
    'us-east1': ['us-east1-b', 'us-east1-c', 'us-east1-d'],
    'us-east4': ['us-east4-a', 'us-east4-b', 'us-east4-c'],
    'us-west2': ['us-west2-b', 'us-west2-c'],
    'us-west4': ['us-west4-a', 'us-west4-b'],
    'europe-west4': ['europe-west4-a'],
    'asia-east1': ['asia-east1-c'],
}

# https://cloud.google.com/compute/docs/gpus/gpu-regions-zones
_ZONE_TO_AVAILABLE_GPUS = {
    'us-west1-a': ['T4', 'V100', 'P100'],
    'us-west1-b': ['A100', 'T4', 'V100', 'P100', 'K80'],
    'us-central1-a': ['A100', 'T4', 'V100', 'P4', 'K80'],
    'us-central1-b': ['A100', 'T4', 'V100'],
    'us-central1-c': ['A100', 'T4', 'P4', 'V100', 'P100', 'K80'],
    'us-central1-f': ['A100', 'T4', 'V100', 'P100'],
    'us-east1-b': ['A100', 'P100'],
    'us-east1-c': ['T4', 'V100', 'P100', 'K80'],
    'us-east1-d': ['T4', 'K80'],
    'us-east4-a': ['P4'],
    'us-east4-b': ['T4', 'P4'],
    'us-east4-c': ['T4', 'P4'],
    'us-west2-b': ['T4', 'P4'],
    'us-west2-c': ['T4', 'P4'],
    'us-west4-a': ['T4'],
    'us-west4-b': ['A100', 'T4'], # except a2-megagpu-16g
    'europe-west4-a': [], # A100, T4, V100, P100
    'asia-east1-c': [], # T4, V100, P100
}

_TPU_TO_AVAILABLE_ZONES = {
    'tpu-v2-8': ['us-central1-b', 'us-central1-c', 'us-central1-f', 'europe-west4-a', 'asia-east1-c'],
    'tpu-v2-32': ['us-central1-a', 'europe-west4-a'],
    'tpu-v2-128': ['us-central1-a', 'europe-west4-a'],
    'tpu-v2-256': ['us-central1-a', 'europe-west4-a'],
    'tpu-v2-512': ['us-central1-a', 'europe-west4-a'],
    'tpu-v3-8': ['us-central1-b', 'us-central1-b', 'us-central1-f', 'europe-west4-a'],
    'tpu-v3-32': ['eruope-west4-a', 'us-east1-d'],
    'tpu-v3-64': ['eruope-west4-a', 'us-east1-d'],
    'tpu-v3-128': ['eruope-west4-a', 'us-east1-d'],
    'tpu-v3-256': ['eruope-west4-a', 'us-east1-d'],
    'tpu-v3-512': ['eruope-west4-a', 'us-east1-d'],
    'tpu-v3-1024': ['eruope-west4-a', 'us-east1-d'],
    'tpu-v3-2048': ['eruope-west4-a', 'us-east1-d'],
}


def get_gpu_tpu_df():
    # https://cloud.google.com/compute/gpus-pricing
    # Region availability only covers US regions (us-*).
    gpu_data = {
        'A100': (
            [1, 2, 4, 8, 16],
            [('us-central1', 2.939, 0.880), ('us-west1', 2.939, 0.880),
             ('us-east1', 2.939, 0.880)],
        ),
        'T4': (
            [1, 2, 4],
            [('us-central1', 0.35, 0.11), ('us-west1', 0.35, 0.11),
             ('us-west2', 0.41, 0.11), ('us-west4', 0.37, 0.069841),
             ('us-east1', 0.35, 0.11), ('us-east4', 0.35, 0.11)],
        ),
        'P4': (
            [1, 2, 4],
            [('us-central1', 0.60, 0.216), ('us-west2', 0.72, 0.2592),
             ('us-west4', 0.60, 0.216), ('us-east4', 0.60, 0.216)],
        ),
        'V100': (
            [1, 2, 4, 8],
            [('us-central1', 2.48, 0.74), ('us-west1', 2.48, 0.74),
             ('us-east1', 2.48, 0.74)],
        ),
        'P100': (
            [1, 2, 4],
            [('us-central1', 1.46, 0.43), ('us-west1', 1.46, 0.43),
             ('us-east1', 1.46, 0.43)],
        ),
        'K80': (
            [1, 2, 4, 8],
            [('us-central1', 0.45, 0.038), ('us-west1', 0.45, 0.038),
             ('us-east1', 0.45, 0.038)],
        ),
    }
    # https://cloud.google.com/tpu/pricing
    # These are the only TPUs that we can launch using our account.
    tpu_data = {
        'tpu-v2-8': ([1], [('us-central1', 4.50, 1.35),
                           ('europe-west4', 4.95, 1.485),
                           ('asia-east1', 5.22, 1.566)]),
        'tpu-v2-32': (
            [1],
            [('us-central1', 24, np.nan), ('europe-west4', 24, np.nan)],
        ),
        'tpu-v2-128': (
            [1],
            [('us-central1', 96, np.nan), ('europe-west4', 96, np.nan)],
        ),
        'tpu-v3-8': (
            [1],
            [('us-central1', 8.00, 2.40), ('europe-west4', 8.80, 2.64)],
        ),
    }
    acc_data = dict(**gpu_data, **tpu_data)
    rows = []
    for acc_name, (counts, regions) in acc_data.items():
        for region, price, spot_price in regions:
            for cnt in counts:
                for zone in _REGION_TO_ZONES[region]:
                    if acc_name.startswith('tpu-'):
                        if zone not in _TPU_TO_AVAILABLE_ZONES[acc_name]:
                            continue
                    elif acc_name not in _ZONE_TO_AVAILABLE_GPUS[zone]:
                            continue
                    # Exceptional case.
                    if (zone == 'us-west4-b' and acc_name == 'A100' and cnt == 16):
                        continue
                    rows.append([
                        None, acc_name, cnt, 0, acc_name, price * cnt,
                        spot_price * cnt, region, zone,
                    ])
    df = pd.DataFrame(
        data=rows,
        columns=[
            'InstanceType',
            'AcceleratorName',
            'AcceleratorCount',
            'MemoryGiB',
            'GpuInfo',
            'Price',
            'SpotPrice',
            'Region',
            'AvailabilityZone',
        ],
    )
    return df


if __name__ == '__main__':
    df = get_gpu_tpu_df()
    df.to_csv('../data/gcp.csv', index=False)
    print('GCP Service Catalog saved to gcp.csv')
