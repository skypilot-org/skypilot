"""Clouds in Sky."""
# TODO: isort:skip is ugly. We should have a better way to do this to avoid
# circular imports.
from sky.clouds.cloud_registry import CLOUD_REGISTRY  # isort:skip
from sky.clouds.cloud import Cloud  # isort:skip
from sky.clouds.cloud import CloudImplementationFeatures  # isort:skip
from sky.clouds.cloud import Region  # isort:skip
from sky.clouds.cloud import Zone  # isort:skip
from sky.clouds.aws import AWS
from sky.clouds.azure import Azure
from sky.clouds.gcp import GCP
from sky.clouds.ibm import IBM
from sky.clouds.kubernetes import Kubernetes
from sky.clouds.lambda_cloud import Lambda
from sky.clouds.local import Local
from sky.clouds.oci import OCI
from sky.clouds.scp import SCP

__all__ = [
    'IBM',
    'AWS',
    'Azure',
    'Cloud',
    'GCP',
    'Lambda',
    'Local',
    'SCP',
    'OCI',
    'Kubernetes',
    'CloudImplementationFeatures',
    'Region',
    'Zone',
    'CLOUD_REGISTRY',
]
