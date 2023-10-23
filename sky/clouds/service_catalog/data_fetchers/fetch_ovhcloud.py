import pandas
import pandas as pd
import requests
import bs4
import json
import re
import csv


GPU_NAMES_OVERWRITE = {
    "V100S-32-GB": "V100-32GB",
    "V100-16-GB": "V100",
    "A100-80-GB": "A100-80GB"
}

GPUS_REGIONS_ZONES = {
    "V100-32GB": {
        'GRA': ['GRA7'],'BHS': ['BHS5'], 'FRA': ['DE1'], 'LON': ['UK1'], 'WAW': ['WAW1']
    },
    "V100": {'GRA': ['GRA9'], 'BHS': ['BHS5']},
    "A100-80GB": {'GRA': ['GRA11']}
}


def fetch_pricing_page():
    html = requests.get('https://www.ovhcloud.com/en/public-cloud/prices/#')
    soup = bs4.BeautifulSoup(html.content, 'lxml')
    container = soup.css.select('#compute')[0].parent
    elems = []
    for div in container.find_all('div', recursive=False):
        h3s = div.css.select('h3.public-cloud-prices-title')
        if h3s:
            try:
                dfs = pd.read_html(str(div))
                tmp_df = pd.concat(dfs)
                if 'GPU' not in tmp_df.columns:
                    continue
                gpus_infos = div.findChildren("tr")[1:]
                regions = [gpu_info.attrs['data-regions'].split(' ') for gpu_info in gpus_infos]
                regions_prices = [json.loads(gpu_info.attrs['data-price']) for gpu_info in gpus_infos]
                tmp_df['regions'] = regions
                tmp_df['regions_prices'] = regions_prices
                tmp_df['Price'] = tmp_df['Price'].str.replace('  /hour', '').str.replace('$', '')
                elems.append(tmp_df)
            except (ValueError, KeyError):
                pass
    df = pd.concat(elems)
    df = df[df['GPU'].notnull() & df['Name'].notnull()]
    df['key'] = df['Name'].str.lower()
    regionalized_df = df.explode('regions')
    regionalized_df['region_price'] = regionalized_df.apply(lambda x: bs4.BeautifulSoup(x['regions_prices'][x['regions']]['linux.hourly']).get_text().split('\n')[1].replace(' $', ''), axis=1)
    return regionalized_df[['Name', 'Memory', 'vCore', 'GPU', 'Storage', 'regions', 'region_price']]


def convert_to_skypilot_format(output_path, regionalized_df: pandas.DataFrame):
    regionalized_df = regionalized_df.rename(columns={'Name': 'InstanceType',
                            'vCore': 'vCPUs',
                            'regions': 'Region',
                            'region_price': 'Price',
                            'Memory': 'MemoryGiB'})
    regionalized_df['GPU_data'] = regionalized_df['GPU'].apply(lambda x: re.split('\W+', x))
    regionalized_df['MemoryGiB'] = regionalized_df['MemoryGiB'].apply(lambda x: re.split('\W+', x)[0])
    regionalized_df['AcceleratorName'] = regionalized_df['GPU_data'].apply(lambda x: GPU_NAMES_OVERWRITE.get(f'{"-".join(x[-3:])}', x[-3]))
    regionalized_df['AcceleratorCount'] = regionalized_df['GPU_data'].apply(lambda x: int(x[0]) if x[0].isdigit() else 1)
    regionalized_df['MemoryInfo'] = regionalized_df['GPU_data'].apply(lambda x: int(x[-2]) * 1024)
    regionalized_df['AvailabilityZone'] = regionalized_df.apply(lambda x: GPUS_REGIONS_ZONES[x['AcceleratorName']][x['Region']], axis=1)
    regionalized_df = regionalized_df.explode('AvailabilityZone')
    regionalized_df['GpuInfo'] = regionalized_df.apply(lambda x: {
        'Gpus': [{
                            'Name': x['AcceleratorName'],
                            'Manufacturer': 'NVIDIA',
                            'Count': x['AcceleratorCount'],
                            'MemoryInfo': {
                                'SizeInMiB': x['MemoryInfo']
                            },
                        }],
        'TotalGpuMemoryInMiB': x['MemoryInfo'] * x['AcceleratorCount']
    }, axis=1)
    regionalized_df['SpotPrice'] = None
    regionalized_df.to_csv(output_path, ",", header=True, index=False, columns=[
            'InstanceType', 'AcceleratorName', 'AcceleratorCount', 'vCPUs',
            'MemoryGiB', 'Price', 'Region', 'GpuInfo', 'SpotPrice', 'AvailabilityZone'
        ])
    return regionalized_df


if __name__ == "__main__":
    prices = fetch_pricing_page()
    prices
    convert_to_skypilot_format('./vms.csv', prices)