import os

# This file is in '<project_root>/sky/utils/directory_utils.py'
# So we need to go up 2 levels to get to the '<project_root>/sky' directory
SKY_DIR = os.path.dirname(os.path.dirname(__file__))


def get_sky_dir():
    """Get the sky root directory."""
    return SKY_DIR


# sky dir is in '<project_root>/sky'
# So we need to go up 1 level to get to the '<project_root>' directory
PROJECT_ROOT_DIR = os.path.dirname(SKY_DIR)


def get_project_root_dir():
    """Get the project root directory."""
    return PROJECT_ROOT_DIR
