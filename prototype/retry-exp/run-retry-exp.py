import collections
import io
import os
import re
import subprocess

import pandas as pd

from sky.clouds.service_catalog import common


debug_series = {'K80': [1]}
p_series = {'V100': [1,4,8], 'K80': [1, 8, 16], 'A100': [8]}
g_series = {'T4': [1,4,8], 'M60': [1, 2, 4], 'A10G': [1,4,8], 'K520':[1,4], 'T4g': [1, 2]}
PATTERN = r'Got (.*) in '
SECOND_ERR_PATTERN = r'An error occurred \((.*?)\)'
RESULT_PATH = './result.csv'


# Configs
cloud = 'aws'
TEST_SERIES = debug_series
common.catalog_config['_faster_retry_by_catalog'] = False
# Only retry the region/zones that are in the area. This is cloud specific.
common.catalog_config['_retry_area'] = ["us", "america"] # us, eu, ap or more specific us-east, us-west-1, etc.
# Shuffle the order of regions to be tried
common.catalog_config['_shuffle_regions'] = True

launch_yaml = """
resources:
  cloud: {cloud}
  accelerators:
    {gpu}: {cnt}
  use_spot: true
"""

df = pd.DataFrame(columns=['cloud', 'gpu', 'gpu_count', 'spot', 'retry_count', 'status', 'detailed_error_count'])
# Resume from last run
if os.path.exists(RESULT_PATH):
    df = pd.read_csv(RESULT_PATH)


def parse_outputs(lines: str, exp_status: dict):
    error_count = collections.defaultdict(int)
    
    succeeded = True
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
        if 'sky.exceptions.ResourcesUnavailableError' in line:
            succeeded = False
    exp_status['retry_count'] = sum(error_count.values())
    exp_status['detailed_error_count'] = dict(error_count)
    exp_status['status'] = 'SUCCEEDED' if succeeded else 'FAILED'
    return exp_status

    

for gpu, cnt_list in TEST_SERIES.items():
    print(f'==== GPU: {gpu} ====')
    for cnt in cnt_list:
        print(f'{gpu}: {cnt}...', end='', flush=True)
        res_df = df[(df['gpu'] == gpu) & (df['gpu_count'] == cnt)]
        if len(res_df) > 0:
            print('Found in result. Skip.')
            continue
        
        new_yaml = launch_yaml.format(cloud=cloud, gpu=gpu, cnt=cnt)
        with open('./tmp.yaml', 'w') as f:
            f.write(new_yaml)
            
        cmd = 'sky launch -c retry-exp ./tmp.yaml'
        # print(cmd)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True, preexec_fn=os.setsid)
        
        lines = []
        for line in io.TextIOWrapper(proc.stdout, encoding='utf-8'):
            if 'Shared connection to ' in line:
                break
            lines.append(line)

        down_cmd = 'sky down retry-exp'
        proc = subprocess.run(down_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)

        exp_status = dict(
            cloud='aws',
            gpu=gpu,
            gpu_count=cnt,
            spot=True,
        )
        exp_status = parse_outputs(lines, exp_status)
        df = df.append(exp_status, ignore_index=True)
        df.to_csv(RESULT_PATH, index=False)
        
        print('Done.')
