"""Directory utilities."""

import os

# This file is in '<project_root>/sky/utils/directory_utils.py'
# So we need to go up 2 levels to get to the '<project_root>/sky' directory
SKY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_sky_dir():
    """Get the sky root directory."""
    return SKY_DIR
