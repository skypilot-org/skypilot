"""Plugin extensions module.

This module provides extension points that plugins can hook into to provide
custom functionality.
"""
from sky.utils.plugin_extensions.cluster_state_source import CachedObjects
from sky.utils.plugin_extensions.cluster_state_source import ClusterStateSource
from sky.utils.plugin_extensions.cluster_state_source import ObjectQuery
from sky.utils.plugin_extensions.cluster_state_source import Refusal
from sky.utils.plugin_extensions.external_failure_source import (
    ExternalClusterFailure)
from sky.utils.plugin_extensions.external_failure_source import (
    ExternalFailureSource)
from sky.utils.plugin_extensions.log_delivery_source import LogDeliverySource
from sky.utils.plugin_extensions.node_info_source import KubernetesNodeSnapshot
from sky.utils.plugin_extensions.node_info_source import NodeInfoSource
from sky.utils.plugin_extensions.pod_info_source import PodInfoSource
from sky.utils.plugin_extensions.recipe_validator import RecipeValidator

__all__ = [
    'CachedObjects',
    'ClusterStateSource',
    'ExternalClusterFailure',
    'ExternalFailureSource',
    'KubernetesNodeSnapshot',
    'LogDeliverySource',
    'NodeInfoSource',
    'ObjectQuery',
    'PodInfoSource',
    'RecipeValidator',
    'Refusal',
]
