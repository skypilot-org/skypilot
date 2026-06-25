"""Slurm provisioner for SkyPilot."""

from sky.provision.slurm.config import bootstrap_instances
from sky.provision.slurm.instance import cleanup_ports
from sky.provision.slurm.instance import get_cluster_info
from sky.provision.slurm.instance import get_command_runners
from sky.provision.slurm.instance import open_ports
from sky.provision.slurm.instance import query_instances
from sky.provision.slurm.instance import run_instances
from sky.provision.slurm.instance import stop_instances
from sky.provision.slurm.instance import template_override
from sky.provision.slurm.instance import terminate_instances
from sky.provision.slurm.instance import wait_instances

# Note: template_override is registered in sky/provision/__init__.py (after
# register_provisioner is defined), since this module is imported there before
# that definition exists.
