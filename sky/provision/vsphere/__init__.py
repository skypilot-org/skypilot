"""Vsphere provisioner for SkyPilot."""

from sky.provision.vsphere.config import bootstrap_instances
from sky.provision.vsphere.instance import cleanup_ports
from sky.provision.vsphere.instance import get_cluster_info
from sky.provision.vsphere.instance import open_ports
from sky.provision.vsphere.instance import query_instances
from sky.provision.vsphere.instance import run_instances
from sky.provision.vsphere.instance import stop_instances
from sky.provision.vsphere.instance import terminate_instances
from sky.provision.vsphere.instance import wait_instances

__all__ = (
    'bootstrap_instances',
    'run_instances',
    'stop_instances',
    'terminate_instances',
    'wait_instances',
    'get_cluster_info',
    'open_ports',
    'cleanup_ports',
    'query_instances',
)
