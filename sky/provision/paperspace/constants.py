"""Constants for Paperspace provisioner
(TODO) asaiacai: fetch default images using API
The H100 and A100 family of GPUs use `Ubuntu 22.04 MLaiB`
V100s use Ubuntu `20.04 MLaiB`
CPU instances use `Ubuntu 22.04 Server`
These instances are fetched using:
    # curl -X GET 'https://api.paperspace.io/templates/getTemplates \
    # -H 'X-Api-Key: ...'
"""

CPU_INSTANCES_TEMPLATEID = {f'C{i}': 't0nspur5' for i in range(5, 10)}

INSTANCE_TO_TEMPLATEID = {
    'H100x8': 't7vp562h',
    'H100': 'tilqt47t',
    'A100-80Gx8': 'tvimtol9',
    'A100-80G': 'tqqsxr6b',
    'V100-32Gx4': 'twnlo3zj',
    'V100-32Gx2': 'twnlo3zj',
    'V100-32G': 'twnlo3zj',
    'V100': 'twnlo3zj',
    'GPU+': 'twnlo3zj',
    'P4000': 'twnlo3zj',
    'P4000x2': 'twnlo3zj',
    'A4000': 'twnlo3zj',
    'A4000x2': 'twnlo3zj',
    'A4000x4': 'twnlo3zj',
    **CPU_INSTANCES_TEMPLATEID
}
NVLINK_INSTANCES = {
    'H100x8',
    'A100-80Gx8',
}

DISK_SIZES = [100, 250, 500, 1000, 2000]
