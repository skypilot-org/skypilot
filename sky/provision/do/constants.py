"""DO cloud constants
"""

POLL_INTERVAL = 5
WAIT_DELETE_VOLUMES = 5

GPU_IMAGES = {
    'gpu-h100x1-80gb': 'gpu-h100x1-base',
    'gpu-h100x8-640gb': 'gpu-h100x8-base',
}

INSTALL_DOCKER = ('#!/bin/bash\n'
                  'if ! command -v docker &> /dev/null; then \n'
                  'sudo apt install -y docker.io \n'
                  'fi \n')
