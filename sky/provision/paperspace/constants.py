"""Constants for Paperspace provisioner"""

CPU_INSTANCES_TEMPLATEID = {f'C{i}': 't0nspur5' for i in range(5, 10)}

INSTANCE_TO_TEMPLATEID = {
    'H100x8': 'tvimtol9',
    'H100': 'tiq8mhti',
    'A100-80Gx8': 'tvimtol9',
    'A100-80Gx4': 'tiq8mhti',
    'A100-80Gx2': 'tiq8mhti',
    'A100-80G': 'tiq8mhti',
    'V100-32Gx4': 'twnlo3zj',
    'V100-32Gx2': 'twnlo3zj',
    'V100-32G': 'twnlo3zj',
    'V100': 'twnlo3zj',
    **CPU_INSTANCES_TEMPLATEID
}
NVLINK_INSTANCES = {
    'H100x8',
    'A100-80Gx8',
}

DISK_SIZES = [100, 250, 500, 1000, 2000]
