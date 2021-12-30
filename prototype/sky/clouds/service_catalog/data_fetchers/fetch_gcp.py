"""A script that generates Google Cloud GPU catalog.

Google Cloud does not have an API for querying TPU/GPU offerings, so this
part is currently hard-coded.

TODO: Add support for regular VMs
https://cloud.google.com/sdk/gcloud/reference/compute/machine-types/list
"""

import json

from absl import app
from absl import logging
import pandas as pd


def get_gpu_tpu_df():
    # All prices are for us-central1 region.
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
    tpu_data = {
        'tpu-v2-8': (
            [1],
            [('us-central1', 4.50, 1.35), ('europe-west4', 4.95, 1.485),
             ('asia-east1', 5.22, 1.566)],
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
                rows.append([
                    None, acc_name, cnt, 0, acc_name, price * cnt,
                    spot_price * cnt, region
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
        ],
    )
    return df


def main(argv):
    del argv  # Unused.
    logging.set_verbosity(logging.DEBUG)
    df = get_gpu_tpu_df()
    df.to_csv('../data/gcp.csv', index=False)
    print('GCP Service Catalog saved to gcp.csv')


if __name__ == '__main__':
    app.run(main)
