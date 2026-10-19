"""Modal | Catalog

Loads the service catalog and can be used to query instance types and
pricing information for Modal. The catalog CSV is seeded from the
hardcoded _CATALOG_CSV_CONTENTS string at import time, since Modal is
not on the SkyPilot hosted catalog server.
"""

import os
import tempfile
import typing
from typing import Dict, List, Optional, Tuple, Union

from sky.catalog import common

if typing.TYPE_CHECKING:
    from sky.clouds import cloud

# Pricing snapshot: 2026-06-25. Source: https://modal.com/pricing
# Per-GPU vCPU and RAM values are [ASSUMED] — Modal does not publish container
# sizing per GPU tier; values are estimated from comparable providers (A1, A2
# in the Phase 2 research assumptions log). Prices are per-GPU hourly, scaled
# linearly with GPU count.
_CATALOG_CSV_CONTENTS = """\
InstanceType,AcceleratorName,AcceleratorCount,vCPUs,MemoryGiB,Price,SpotPrice,Region,GpuInfo
1x_T4,T4,1.0,8.0,29.0,0.59,,us,"{'Gpus': [{'Name': 'T4', 'Manufacturer': 'NVIDIA', 'Count': 1, 'MemoryInfo': {'SizeInMiB': 16384}}], 'TotalGpuMemoryInMiB': 16384}"
2x_T4,T4,2.0,16.0,58.0,1.18,,us,"{'Gpus': [{'Name': 'T4', 'Manufacturer': 'NVIDIA', 'Count': 2, 'MemoryInfo': {'SizeInMiB': 16384}}], 'TotalGpuMemoryInMiB': 32768}"
3x_T4,T4,3.0,24.0,87.0,1.77,,us,"{'Gpus': [{'Name': 'T4', 'Manufacturer': 'NVIDIA', 'Count': 3, 'MemoryInfo': {'SizeInMiB': 16384}}], 'TotalGpuMemoryInMiB': 49152}"
4x_T4,T4,4.0,32.0,116.0,2.36,,us,"{'Gpus': [{'Name': 'T4', 'Manufacturer': 'NVIDIA', 'Count': 4, 'MemoryInfo': {'SizeInMiB': 16384}}], 'TotalGpuMemoryInMiB': 65536}"
5x_T4,T4,5.0,40.0,145.0,2.95,,us,"{'Gpus': [{'Name': 'T4', 'Manufacturer': 'NVIDIA', 'Count': 5, 'MemoryInfo': {'SizeInMiB': 16384}}], 'TotalGpuMemoryInMiB': 81920}"
6x_T4,T4,6.0,48.0,174.0,3.54,,us,"{'Gpus': [{'Name': 'T4', 'Manufacturer': 'NVIDIA', 'Count': 6, 'MemoryInfo': {'SizeInMiB': 16384}}], 'TotalGpuMemoryInMiB': 98304}"
7x_T4,T4,7.0,56.0,203.0,4.13,,us,"{'Gpus': [{'Name': 'T4', 'Manufacturer': 'NVIDIA', 'Count': 7, 'MemoryInfo': {'SizeInMiB': 16384}}], 'TotalGpuMemoryInMiB': 114688}"
8x_T4,T4,8.0,64.0,232.0,4.72,,us,"{'Gpus': [{'Name': 'T4', 'Manufacturer': 'NVIDIA', 'Count': 8, 'MemoryInfo': {'SizeInMiB': 16384}}], 'TotalGpuMemoryInMiB': 131072}"
1x_L4,L4,1.0,8.0,29.0,0.8,,us,"{'Gpus': [{'Name': 'L4', 'Manufacturer': 'NVIDIA', 'Count': 1, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 24576}"
2x_L4,L4,2.0,16.0,58.0,1.6,,us,"{'Gpus': [{'Name': 'L4', 'Manufacturer': 'NVIDIA', 'Count': 2, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 49152}"
3x_L4,L4,3.0,24.0,87.0,2.4,,us,"{'Gpus': [{'Name': 'L4', 'Manufacturer': 'NVIDIA', 'Count': 3, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 73728}"
4x_L4,L4,4.0,32.0,116.0,3.2,,us,"{'Gpus': [{'Name': 'L4', 'Manufacturer': 'NVIDIA', 'Count': 4, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 98304}"
5x_L4,L4,5.0,40.0,145.0,4.0,,us,"{'Gpus': [{'Name': 'L4', 'Manufacturer': 'NVIDIA', 'Count': 5, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 122880}"
6x_L4,L4,6.0,48.0,174.0,4.8,,us,"{'Gpus': [{'Name': 'L4', 'Manufacturer': 'NVIDIA', 'Count': 6, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 147456}"
7x_L4,L4,7.0,56.0,203.0,5.6,,us,"{'Gpus': [{'Name': 'L4', 'Manufacturer': 'NVIDIA', 'Count': 7, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 172032}"
8x_L4,L4,8.0,64.0,232.0,6.4,,us,"{'Gpus': [{'Name': 'L4', 'Manufacturer': 'NVIDIA', 'Count': 8, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 196608}"
1x_A10,A10,1.0,12.0,46.0,1.1,,us,"{'Gpus': [{'Name': 'A10', 'Manufacturer': 'NVIDIA', 'Count': 1, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 24576}"
2x_A10,A10,2.0,24.0,92.0,2.2,,us,"{'Gpus': [{'Name': 'A10', 'Manufacturer': 'NVIDIA', 'Count': 2, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 49152}"
3x_A10,A10,3.0,36.0,138.0,3.3,,us,"{'Gpus': [{'Name': 'A10', 'Manufacturer': 'NVIDIA', 'Count': 3, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 73728}"
4x_A10,A10,4.0,48.0,184.0,4.4,,us,"{'Gpus': [{'Name': 'A10', 'Manufacturer': 'NVIDIA', 'Count': 4, 'MemoryInfo': {'SizeInMiB': 24576}}], 'TotalGpuMemoryInMiB': 98304}"
1x_L40S,L40S,1.0,12.0,46.0,1.95,,us,"{'Gpus': [{'Name': 'L40S', 'Manufacturer': 'NVIDIA', 'Count': 1, 'MemoryInfo': {'SizeInMiB': 49152}}], 'TotalGpuMemoryInMiB': 49152}"
2x_L40S,L40S,2.0,24.0,92.0,3.9,,us,"{'Gpus': [{'Name': 'L40S', 'Manufacturer': 'NVIDIA', 'Count': 2, 'MemoryInfo': {'SizeInMiB': 49152}}], 'TotalGpuMemoryInMiB': 98304}"
3x_L40S,L40S,3.0,36.0,138.0,5.85,,us,"{'Gpus': [{'Name': 'L40S', 'Manufacturer': 'NVIDIA', 'Count': 3, 'MemoryInfo': {'SizeInMiB': 49152}}], 'TotalGpuMemoryInMiB': 147456}"
4x_L40S,L40S,4.0,48.0,184.0,7.8,,us,"{'Gpus': [{'Name': 'L40S', 'Manufacturer': 'NVIDIA', 'Count': 4, 'MemoryInfo': {'SizeInMiB': 49152}}], 'TotalGpuMemoryInMiB': 196608}"
5x_L40S,L40S,5.0,60.0,230.0,9.75,,us,"{'Gpus': [{'Name': 'L40S', 'Manufacturer': 'NVIDIA', 'Count': 5, 'MemoryInfo': {'SizeInMiB': 49152}}], 'TotalGpuMemoryInMiB': 245760}"
6x_L40S,L40S,6.0,72.0,276.0,11.7,,us,"{'Gpus': [{'Name': 'L40S', 'Manufacturer': 'NVIDIA', 'Count': 6, 'MemoryInfo': {'SizeInMiB': 49152}}], 'TotalGpuMemoryInMiB': 294912}"
7x_L40S,L40S,7.0,84.0,322.0,13.65,,us,"{'Gpus': [{'Name': 'L40S', 'Manufacturer': 'NVIDIA', 'Count': 7, 'MemoryInfo': {'SizeInMiB': 49152}}], 'TotalGpuMemoryInMiB': 344064}"
8x_L40S,L40S,8.0,96.0,368.0,15.6,,us,"{'Gpus': [{'Name': 'L40S', 'Manufacturer': 'NVIDIA', 'Count': 8, 'MemoryInfo': {'SizeInMiB': 49152}}], 'TotalGpuMemoryInMiB': 393216}"
1x_A100-40GB,A100-40GB,1.0,12.0,46.0,2.1,,us,"{'Gpus': [{'Name': 'A100-40GB', 'Manufacturer': 'NVIDIA', 'Count': 1, 'MemoryInfo': {'SizeInMiB': 40960}}], 'TotalGpuMemoryInMiB': 40960}"
2x_A100-40GB,A100-40GB,2.0,24.0,92.0,4.2,,us,"{'Gpus': [{'Name': 'A100-40GB', 'Manufacturer': 'NVIDIA', 'Count': 2, 'MemoryInfo': {'SizeInMiB': 40960}}], 'TotalGpuMemoryInMiB': 81920}"
3x_A100-40GB,A100-40GB,3.0,36.0,138.0,6.3,,us,"{'Gpus': [{'Name': 'A100-40GB', 'Manufacturer': 'NVIDIA', 'Count': 3, 'MemoryInfo': {'SizeInMiB': 40960}}], 'TotalGpuMemoryInMiB': 122880}"
4x_A100-40GB,A100-40GB,4.0,48.0,184.0,8.4,,us,"{'Gpus': [{'Name': 'A100-40GB', 'Manufacturer': 'NVIDIA', 'Count': 4, 'MemoryInfo': {'SizeInMiB': 40960}}], 'TotalGpuMemoryInMiB': 163840}"
5x_A100-40GB,A100-40GB,5.0,60.0,230.0,10.5,,us,"{'Gpus': [{'Name': 'A100-40GB', 'Manufacturer': 'NVIDIA', 'Count': 5, 'MemoryInfo': {'SizeInMiB': 40960}}], 'TotalGpuMemoryInMiB': 204800}"
6x_A100-40GB,A100-40GB,6.0,72.0,276.0,12.6,,us,"{'Gpus': [{'Name': 'A100-40GB', 'Manufacturer': 'NVIDIA', 'Count': 6, 'MemoryInfo': {'SizeInMiB': 40960}}], 'TotalGpuMemoryInMiB': 245760}"
7x_A100-40GB,A100-40GB,7.0,84.0,322.0,14.7,,us,"{'Gpus': [{'Name': 'A100-40GB', 'Manufacturer': 'NVIDIA', 'Count': 7, 'MemoryInfo': {'SizeInMiB': 40960}}], 'TotalGpuMemoryInMiB': 286720}"
8x_A100-40GB,A100-40GB,8.0,96.0,368.0,16.8,,us,"{'Gpus': [{'Name': 'A100-40GB', 'Manufacturer': 'NVIDIA', 'Count': 8, 'MemoryInfo': {'SizeInMiB': 40960}}], 'TotalGpuMemoryInMiB': 327680}"
1x_A100-80GB,A100-80GB,1.0,12.0,46.0,2.5,,us,"{'Gpus': [{'Name': 'A100-80GB', 'Manufacturer': 'NVIDIA', 'Count': 1, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 81920}"
2x_A100-80GB,A100-80GB,2.0,24.0,92.0,5.0,,us,"{'Gpus': [{'Name': 'A100-80GB', 'Manufacturer': 'NVIDIA', 'Count': 2, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 163840}"
3x_A100-80GB,A100-80GB,3.0,36.0,138.0,7.5,,us,"{'Gpus': [{'Name': 'A100-80GB', 'Manufacturer': 'NVIDIA', 'Count': 3, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 245760}"
4x_A100-80GB,A100-80GB,4.0,48.0,184.0,10.0,,us,"{'Gpus': [{'Name': 'A100-80GB', 'Manufacturer': 'NVIDIA', 'Count': 4, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 327680}"
5x_A100-80GB,A100-80GB,5.0,60.0,230.0,12.5,,us,"{'Gpus': [{'Name': 'A100-80GB', 'Manufacturer': 'NVIDIA', 'Count': 5, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 409600}"
6x_A100-80GB,A100-80GB,6.0,72.0,276.0,15.0,,us,"{'Gpus': [{'Name': 'A100-80GB', 'Manufacturer': 'NVIDIA', 'Count': 6, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 491520}"
7x_A100-80GB,A100-80GB,7.0,84.0,322.0,17.5,,us,"{'Gpus': [{'Name': 'A100-80GB', 'Manufacturer': 'NVIDIA', 'Count': 7, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 573440}"
8x_A100-80GB,A100-80GB,8.0,96.0,368.0,20.0,,us,"{'Gpus': [{'Name': 'A100-80GB', 'Manufacturer': 'NVIDIA', 'Count': 8, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 655360}"
1x_RTX-PRO-6000,RTX-PRO-6000,1.0,14.0,60.0,3.03,,us,"{'Gpus': [{'Name': 'RTX-PRO-6000', 'Manufacturer': 'NVIDIA', 'Count': 1, 'MemoryInfo': {'SizeInMiB': 98304}}], 'TotalGpuMemoryInMiB': 98304}"
2x_RTX-PRO-6000,RTX-PRO-6000,2.0,28.0,120.0,6.06,,us,"{'Gpus': [{'Name': 'RTX-PRO-6000', 'Manufacturer': 'NVIDIA', 'Count': 2, 'MemoryInfo': {'SizeInMiB': 98304}}], 'TotalGpuMemoryInMiB': 196608}"
3x_RTX-PRO-6000,RTX-PRO-6000,3.0,42.0,180.0,9.09,,us,"{'Gpus': [{'Name': 'RTX-PRO-6000', 'Manufacturer': 'NVIDIA', 'Count': 3, 'MemoryInfo': {'SizeInMiB': 98304}}], 'TotalGpuMemoryInMiB': 294912}"
4x_RTX-PRO-6000,RTX-PRO-6000,4.0,56.0,240.0,12.12,,us,"{'Gpus': [{'Name': 'RTX-PRO-6000', 'Manufacturer': 'NVIDIA', 'Count': 4, 'MemoryInfo': {'SizeInMiB': 98304}}], 'TotalGpuMemoryInMiB': 393216}"
5x_RTX-PRO-6000,RTX-PRO-6000,5.0,70.0,300.0,15.15,,us,"{'Gpus': [{'Name': 'RTX-PRO-6000', 'Manufacturer': 'NVIDIA', 'Count': 5, 'MemoryInfo': {'SizeInMiB': 98304}}], 'TotalGpuMemoryInMiB': 491520}"
6x_RTX-PRO-6000,RTX-PRO-6000,6.0,84.0,360.0,18.18,,us,"{'Gpus': [{'Name': 'RTX-PRO-6000', 'Manufacturer': 'NVIDIA', 'Count': 6, 'MemoryInfo': {'SizeInMiB': 98304}}], 'TotalGpuMemoryInMiB': 589824}"
7x_RTX-PRO-6000,RTX-PRO-6000,7.0,98.0,420.0,21.21,,us,"{'Gpus': [{'Name': 'RTX-PRO-6000', 'Manufacturer': 'NVIDIA', 'Count': 7, 'MemoryInfo': {'SizeInMiB': 98304}}], 'TotalGpuMemoryInMiB': 688128}"
8x_RTX-PRO-6000,RTX-PRO-6000,8.0,112.0,480.0,24.24,,us,"{'Gpus': [{'Name': 'RTX-PRO-6000', 'Manufacturer': 'NVIDIA', 'Count': 8, 'MemoryInfo': {'SizeInMiB': 98304}}], 'TotalGpuMemoryInMiB': 786432}"
1x_H100,H100,1.0,20.0,92.0,3.95,,us,"{'Gpus': [{'Name': 'H100', 'Manufacturer': 'NVIDIA', 'Count': 1, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 81920}"
2x_H100,H100,2.0,40.0,184.0,7.9,,us,"{'Gpus': [{'Name': 'H100', 'Manufacturer': 'NVIDIA', 'Count': 2, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 163840}"
3x_H100,H100,3.0,60.0,276.0,11.85,,us,"{'Gpus': [{'Name': 'H100', 'Manufacturer': 'NVIDIA', 'Count': 3, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 245760}"
4x_H100,H100,4.0,80.0,368.0,15.8,,us,"{'Gpus': [{'Name': 'H100', 'Manufacturer': 'NVIDIA', 'Count': 4, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 327680}"
5x_H100,H100,5.0,100.0,460.0,19.75,,us,"{'Gpus': [{'Name': 'H100', 'Manufacturer': 'NVIDIA', 'Count': 5, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 409600}"
6x_H100,H100,6.0,120.0,552.0,23.7,,us,"{'Gpus': [{'Name': 'H100', 'Manufacturer': 'NVIDIA', 'Count': 6, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 491520}"
7x_H100,H100,7.0,140.0,644.0,27.65,,us,"{'Gpus': [{'Name': 'H100', 'Manufacturer': 'NVIDIA', 'Count': 7, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 573440}"
8x_H100,H100,8.0,160.0,736.0,31.6,,us,"{'Gpus': [{'Name': 'H100', 'Manufacturer': 'NVIDIA', 'Count': 8, 'MemoryInfo': {'SizeInMiB': 81920}}], 'TotalGpuMemoryInMiB': 655360}"
1x_H200,H200,1.0,20.0,100.0,4.54,,us,"{'Gpus': [{'Name': 'H200', 'Manufacturer': 'NVIDIA', 'Count': 1, 'MemoryInfo': {'SizeInMiB': 144384}}], 'TotalGpuMemoryInMiB': 144384}"
2x_H200,H200,2.0,40.0,200.0,9.08,,us,"{'Gpus': [{'Name': 'H200', 'Manufacturer': 'NVIDIA', 'Count': 2, 'MemoryInfo': {'SizeInMiB': 144384}}], 'TotalGpuMemoryInMiB': 288768}"
3x_H200,H200,3.0,60.0,300.0,13.62,,us,"{'Gpus': [{'Name': 'H200', 'Manufacturer': 'NVIDIA', 'Count': 3, 'MemoryInfo': {'SizeInMiB': 144384}}], 'TotalGpuMemoryInMiB': 433152}"
4x_H200,H200,4.0,80.0,400.0,18.16,,us,"{'Gpus': [{'Name': 'H200', 'Manufacturer': 'NVIDIA', 'Count': 4, 'MemoryInfo': {'SizeInMiB': 144384}}], 'TotalGpuMemoryInMiB': 577536}"
5x_H200,H200,5.0,100.0,500.0,22.7,,us,"{'Gpus': [{'Name': 'H200', 'Manufacturer': 'NVIDIA', 'Count': 5, 'MemoryInfo': {'SizeInMiB': 144384}}], 'TotalGpuMemoryInMiB': 721920}"
6x_H200,H200,6.0,120.0,600.0,27.24,,us,"{'Gpus': [{'Name': 'H200', 'Manufacturer': 'NVIDIA', 'Count': 6, 'MemoryInfo': {'SizeInMiB': 144384}}], 'TotalGpuMemoryInMiB': 866304}"
7x_H200,H200,7.0,140.0,700.0,31.78,,us,"{'Gpus': [{'Name': 'H200', 'Manufacturer': 'NVIDIA', 'Count': 7, 'MemoryInfo': {'SizeInMiB': 144384}}], 'TotalGpuMemoryInMiB': 1010688}"
8x_H200,H200,8.0,160.0,800.0,36.32,,us,"{'Gpus': [{'Name': 'H200', 'Manufacturer': 'NVIDIA', 'Count': 8, 'MemoryInfo': {'SizeInMiB': 144384}}], 'TotalGpuMemoryInMiB': 1155072}"
1x_B200,B200,1.0,24.0,120.0,6.25,,us,"{'Gpus': [{'Name': 'B200', 'Manufacturer': 'NVIDIA', 'Count': 1, 'MemoryInfo': {'SizeInMiB': 184320}}], 'TotalGpuMemoryInMiB': 184320}"
2x_B200,B200,2.0,48.0,240.0,12.5,,us,"{'Gpus': [{'Name': 'B200', 'Manufacturer': 'NVIDIA', 'Count': 2, 'MemoryInfo': {'SizeInMiB': 184320}}], 'TotalGpuMemoryInMiB': 368640}"
3x_B200,B200,3.0,72.0,360.0,18.75,,us,"{'Gpus': [{'Name': 'B200', 'Manufacturer': 'NVIDIA', 'Count': 3, 'MemoryInfo': {'SizeInMiB': 184320}}], 'TotalGpuMemoryInMiB': 552960}"
4x_B200,B200,4.0,96.0,480.0,25.0,,us,"{'Gpus': [{'Name': 'B200', 'Manufacturer': 'NVIDIA', 'Count': 4, 'MemoryInfo': {'SizeInMiB': 184320}}], 'TotalGpuMemoryInMiB': 737280}"
5x_B200,B200,5.0,120.0,600.0,31.25,,us,"{'Gpus': [{'Name': 'B200', 'Manufacturer': 'NVIDIA', 'Count': 5, 'MemoryInfo': {'SizeInMiB': 184320}}], 'TotalGpuMemoryInMiB': 921600}"
6x_B200,B200,6.0,144.0,720.0,37.5,,us,"{'Gpus': [{'Name': 'B200', 'Manufacturer': 'NVIDIA', 'Count': 6, 'MemoryInfo': {'SizeInMiB': 184320}}], 'TotalGpuMemoryInMiB': 1105920}"
7x_B200,B200,7.0,168.0,840.0,43.75,,us,"{'Gpus': [{'Name': 'B200', 'Manufacturer': 'NVIDIA', 'Count': 7, 'MemoryInfo': {'SizeInMiB': 184320}}], 'TotalGpuMemoryInMiB': 1290240}"
8x_B200,B200,8.0,192.0,960.0,50.0,,us,"{'Gpus': [{'Name': 'B200', 'Manufacturer': 'NVIDIA', 'Count': 8, 'MemoryInfo': {'SizeInMiB': 184320}}], 'TotalGpuMemoryInMiB': 1474560}"
"""


def _seed_catalog_if_missing() -> None:
    """Write the bundled Modal catalog CSV to the local catalog dir if absent.

    Modal is not on the SkyPilot hosted catalog server (skypilot-org/
    skypilot-catalog). The standard download path returns HTTP 404. We seed
    the catalog at module import time so the first read_catalog() call finds
    an existing file and never touches the network. This also satisfies the
    T-02-01 tamper-resistance requirement: an existing user-edited catalog is
    never overwritten.
    """
    catalog_path = common.get_catalog_path('modal/vms.csv')
    if os.path.exists(catalog_path):
        return
    catalog_dir = os.path.dirname(catalog_path)
    os.makedirs(catalog_dir, exist_ok=True)
    # Write to a unique temp file then atomically rename, so concurrent imports
    # in multiple processes cannot observe a half-written catalog or clobber
    # each other. os.replace is atomic on POSIX and Windows.
    fd, tmp_path = tempfile.mkstemp(prefix='.modal-vms-',
                                    suffix='.csv.tmp',
                                    dir=catalog_dir)
    renamed = False
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(_CATALOG_CSV_CONTENTS)
        # Re-check under the same call: another process may have seeded it
        # while we were writing. Only the first writer's rename wins; a
        # user-edited catalog is never overwritten (T-02-01).
        if not os.path.exists(catalog_path):
            os.replace(tmp_path, catalog_path)
            renamed = True
    finally:
        if not renamed and os.path.exists(tmp_path):
            os.unlink(tmp_path)


_seed_catalog_if_missing()
# pull_frequency_hours=None: never re-fetch from the hosted catalog server.
# Modal is not on skypilot-org/skypilot-catalog; a fetch would return HTTP 404
# and break sky show-gpus for all clouds. Once seeded above, the file exists
# and _need_update() returns False, keeping the optimizer path fully offline.
_df = common.read_catalog('modal/vms.csv', pull_frequency_hours=None)


def instance_type_exists(instance_type: str) -> bool:
    return common.instance_type_exists_impl(_df, instance_type)


def validate_region_zone(
        region: Optional[str],
        zone: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    # Modal has no availability zones, and its catalog has no
    # `AvailabilityZone` column. `common.validate_region_zone_impl` (via
    # `_filter_region_zone`) would `KeyError` on that column if a zone were
    # passed, so reject zones here with a clean ValueError. Cloud inference
    # treats this as "Modal cannot satisfy this zone" and moves on.
    if zone is not None:
        raise ValueError(
            f'Modal does not support availability zones (got {zone!r}).')
    return common.validate_region_zone_impl('modal', _df, region, zone)


def get_hourly_cost(instance_type: str,
                    use_spot: bool = False,
                    region: Optional[str] = None,
                    zone: Optional[str] = None) -> float:
    """Returns the cost, or the cheapest cost among all zones for spot."""
    return common.get_hourly_cost_impl(_df, instance_type, use_spot, region,
                                       zone)


def get_vcpus_mem_from_instance_type(
        instance_type: str) -> Tuple[Optional[float], Optional[float]]:
    return common.get_vcpus_mem_from_instance_type_impl(_df, instance_type)


def get_default_instance_type(
        cpus: Optional[str] = None,
        memory: Optional[str] = None,
        disk_tier: Optional[str] = None,
        local_disk: Optional[str] = None,
        region: Optional[str] = None,
        zone: Optional[str] = None,
        use_spot: bool = False,
        max_hourly_cost: Optional[float] = None) -> Optional[str]:
    del disk_tier, local_disk  # Modal does not support disk tiers.
    return common.get_instance_type_for_cpus_mem_impl(_df, cpus, memory, region,
                                                      zone, use_spot,
                                                      max_hourly_cost)


def get_accelerators_from_instance_type(
        instance_type: str) -> Optional[Dict[str, Union[int, float]]]:
    return common.get_accelerators_from_instance_type_impl(_df, instance_type)


def get_instance_type_for_accelerator(
    acc_name: str,
    acc_count: int,
    cpus: Optional[str] = None,
    memory: Optional[str] = None,
    use_spot: bool = False,
    local_disk: Optional[str] = None,
    region: Optional[str] = None,
    zone: Optional[str] = None,
    max_hourly_cost: Optional[float] = None
) -> Tuple[Optional[List[str]], List[str]]:
    """Returns a list of instance types that have the given accelerator."""
    del local_disk  # unused
    return common.get_instance_type_for_accelerator_impl(
        df=_df,
        acc_name=acc_name,
        acc_count=acc_count,
        cpus=cpus,
        memory=memory,
        use_spot=use_spot,
        region=region,
        zone=zone,
        max_hourly_cost=max_hourly_cost)


def get_region_zones_for_instance_type(instance_type: str,
                                       use_spot: bool) -> List['cloud.Region']:
    df = _df[_df['InstanceType'] == instance_type]
    return common.get_region_zones(df, use_spot)


def list_accelerators(
        gpus_only: bool,
        name_filter: Optional[str],
        region_filter: Optional[str],
        quantity_filter: Optional[int],
        case_sensitive: bool = True,
        all_regions: bool = False,
        require_price: bool = True) -> Dict[str, List[common.InstanceTypeInfo]]:
    """Returns all instance types in Modal offering GPUs."""
    del require_price  # Unused.
    return common.list_accelerators_impl('Modal', _df, gpus_only, name_filter,
                                         region_filter, quantity_filter,
                                         case_sensitive, all_regions)
