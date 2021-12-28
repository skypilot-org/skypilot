"""A script that generates Google Cloud GPU catalog.

Google Cloud does not have an API for querying TPU/GPU offerings, so this
part is currently hard-coded.

TODO: Add support for regular VMs
https://cloud.google.com/sdk/gcloud/reference/compute/machine-types/list
"""

from absl import app
from absl import flags
from absl import logging
import pandas as pd


def get_gpu_tpu_df():
    # All prices are for us-central1 region.
    # https://cloud.google.com/compute/gpus-pricing
    gpu_data = {
        'A100': (2.939, 0.880, [1, 2, 4, 8, 16]),
        'T4': (0.35, 0.11, [1, 2, 4]),
        'P4': (0.60, 0.216, [1, 2, 4]),
        'V100': (2.48, 0.74, [1, 2, 4, 8]),
        'P100': (1.46, 0.43, [1, 2, 4]),
        'K80': (0.45, 0.038, [1, 2, 4, 8]),
    }
    # https://cloud.google.com/tpu/pricing
    tpu_data = {
        'tpu-v2-8': (4.50, 1.35, [1]),
        'tpu-v3-8': (8.00, 2.40, [1]),
    }
    acc_data = dict(**gpu_data, **tpu_data)
    rows = []
    for acc_name, (price, spot_price, counts) in acc_data.items():
        for cnt in counts:
            rows.append(
                [None, acc_name, cnt, 0, True, price * cnt, spot_price * cnt])
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
