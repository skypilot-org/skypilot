"""Plugin extensions module.

This module provides extension points that plugins can hook into to provide
custom functionality.
"""
from sky.utils.plugin_extensions.external_failure_source import (
    ExternalClusterFailure)
from sky.utils.plugin_extensions.external_failure_source import (
    ExternalFailureSource)
from sky.utils.plugin_extensions.node_info_source import NodeInfoSource

__all__ = [
    'ExternalClusterFailure',
    'ExternalFailureSource',
    'NodeInfoSource',
]
