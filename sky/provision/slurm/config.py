"""Slrum-specific configuration for the provisioner."""
import logging

from sky.provision import common

logger = logging.getLogger(__name__)


def bootstrap_instances(
        region: str, cluster_name: str,
        config: common.ProvisionConfig) -> common.ProvisionConfig:
    """Bootstrap is a no-op for Slurm.

    For both legacy and v1 the cluster-specific directories are created
    by the sbatch script's preamble — there is no long-lived login-node
    state to set up here. v1 in particular relies on
    ``_create_managed_job_v1`` to handle its own preconditions
    (cleanup, drain, reattach) before submitting, so this hook stays a
    pure passthrough. See PLAN.md gap #10.
    """
    del region, cluster_name  # unused.
    return config
