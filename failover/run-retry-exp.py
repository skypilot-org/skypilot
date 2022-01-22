import collections
import io
import json
import os
import re
import subprocess
import time

import pandas as pd

debug_series = {'K80': [1]}
instance_series = {
    'p': {
        'V100': [1, 4, 8],
        'K80': [1, 8, 16],
        'A100': [8]
    },
    'g': {
        'T4': [1, 4, 8],
        'M60': [1, 2, 4],
        'K520': [1, 4],
        # 'T4g': [1, 2], # Need Arm64 AMI
        # 'A10G': [1, 4, 8],
    }
}

SELECTED_REGIONS = {
    'zhwu_g_regions': [
        'us-east-2', 'us-west-2', 'eu-west-2', 'eu-central-1', 'ca-central-1'
    ],
    'zhwu_p_regions': ['us-east-2', 'us-west-2', 'eu-west-3'],
    'all_regions': [],
}

PATTERN = r'Got (.*) in '
SECOND_ERR_PATTERN = r'An error occurred \((.*?)\)'

# Configs
cloud = 'aws'
TEST_SERIES = 'p'
# Whether to keep retry until succeed.
RETRY_UNTIL_SUCCEEDED = True
RETRY_UNTIL_SUCCEEDED_GAP = 60
# Catalog configs
# TEST_REGIONS = 'all_regions'
TEST_REGIONS = 'zhwu_p_regions'
catalog_config = dict(
    _faster_retry_by_catalog=False,
    # Only retry the region/zones that are in the area. This is cloud specific.
    _retry_area=SELECTED_REGIONS[TEST_REGIONS],
    # Shuffle the order of regions to be tried
    _shuffle_regions=True,
)
DEBUG_MODE = True

# Override the catalog configs
from sky.clouds.service_catalog import common

file_dir = os.path.dirname(common.__file__)
config_path = os.path.join(file_dir, '_catalog_config.json')
with open(config_path, 'w') as f:
    json.dump(catalog_config, f)

launch_yaml = """
resources:
  cloud: {cloud}
  accelerators:
    {gpu}: {cnt}
  use_spot: true
"""
RESULT_PATH = f'result_{TEST_SERIES}_{TEST_REGIONS}'
if RETRY_UNTIL_SUCCEEDED:
    RESULT_PATH += '_retry'
RESULT_PATH += '.csv'
TEST_INSTANCE_SERIES = instance_series[TEST_SERIES]

df = pd.DataFrame(columns=[
    'cloud', 'gpu', 'gpu_count', 'spot', 'retry_count', 'status',
    'detailed_error_count'
])
# Resume from last run
if os.path.exists(RESULT_PATH):
    df = pd.read_csv(RESULT_PATH)


def parse_outputs(lines: str, exp_status: dict):
    error_count = collections.defaultdict(int)

    fetch_error_in_next_line = False
    for line in lines:
        if fetch_error_in_next_line:
            match = re.findall(SECOND_ERR_PATTERN, line)
            assert len(match) >= 1, line
            error_count[match[0]] += 1
            fetch_error_in_next_line = False

        match = re.findall(PATTERN, line)
        if len(match) > 0:
            error = match[0]
            if error == 'error(s)':
                fetch_error_in_next_line = True
            else:
                error_count[error] += 1
    exp_status['retry_count'] = sum(error_count.values())
    exp_status['detailed_error_count'] = dict(error_count)
    return exp_status


for gpu, cnt_list in TEST_INSTANCE_SERIES.items():
    print(f'==== GPU: {gpu} ====')
    for cnt in cnt_list:
        print(f'{gpu}: {cnt}...', end='', flush=True)
        exp_status = dict(
            cloud='aws',
            gpu=gpu,
            gpu_count=cnt,
            spot=True,
        )
        res_df = df[(df['gpu'] == gpu) & (df['gpu_count'] == cnt)]
        if len(res_df) > 0:
            print('Found in result. Skip.')
            continue

        new_yaml = launch_yaml.format(cloud=cloud, gpu=gpu, cnt=cnt)
        with open('./tmp.yaml', 'w') as f:
            f.write(new_yaml)

        while True:
            cmd = 'sky launch -c retry-exp ./tmp.yaml'
            # print(cmd)
            proc = subprocess.Popen(cmd,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    shell=True,
                                    preexec_fn=os.setsid)
            succeeded = False
            lines = []
            for line in io.TextIOWrapper(proc.stdout, encoding='utf-8'):
                lines.append(line)
                if 'Shared connection to ' in line:
                    succeeded = True
                    break
                if DEBUG_MODE:
                    print(line, end='')

            down_cmd = 'sky down retry-exp'
            proc = subprocess.run(down_cmd,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT,
                                  shell=True)

            exp_status = parse_outputs(lines, exp_status)
            exp_status['status'] = 'SUCCEEDED' if succeeded else 'FAILED'
            if exp_status['status'] == 'SUCCEEDED':
                break
            print("\nFailed to launch.")
            if RETRY_UNTIL_SUCCEEDED:
                print(f'Retry in {RETRY_UNTIL_SUCCEEDED_GAP} seconds...')
                time.sleep(RETRY_UNTIL_SUCCEEDED_GAP)
            else:
                break
        df = df.append(exp_status, ignore_index=True)
        df.to_csv(RESULT_PATH, index=False)

        print('Done.')
