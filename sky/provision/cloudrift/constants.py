"""CloudRift cloud constants
"""

POLL_INTERVAL = 5
WAIT_DELETE_VOLUMES = 5

# Add any CloudRift-specific GPU images or other constants here
GPU_IMAGES = {
    # Format: 'instance-type': 'image-name'
    'gpu-a100x1-80gb': 'gpu-a100x1-base',
    'gpu-a100x8-640gb': 'gpu-a100x8-base',
}
