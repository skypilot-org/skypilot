"""Cloud provision interface.

This module provides a standard low-level interface that all
providers supported by SkyPilot need to follow.
"""
from typing import Any, Dict, List, Optional

import functools
import importlib
import inspect

from sky import status_lib


def _route_to_cloud_impl(func):

    @functools.wraps(func)
    def _wrapper(*args, **kwargs):
        # check the signature to fail early
        inspect.signature(func).bind(*args, **kwargs)
        if args:
            provider_name = args[0]
            args = args[1:]
        else:
            provider_name = kwargs.pop('provider_name')

        module_name = provider_name
        module = importlib.import_module(f'sky.provision.{module_name.lower()}')

        impl = getattr(module, func.__name__)
        return impl(*args, **kwargs)

    return _wrapper


# pylint: disable=unused-argument

# TODO(suquark): Bring all other functions here from the


@_route_to_cloud_impl
def query_instances(
    provider_name: str,
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Optional[status_lib.ClusterStatus]]:
    """Query instances.

    Returns a dictionary of instance IDs and status.

    A None status means the instance is marked as "terminated"
    or "terminating".
    """
    raise NotImplementedError


@_route_to_cloud_impl
def stop_instances(
    provider_name: str,
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Stop running instances."""
    raise NotImplementedError


@_route_to_cloud_impl
def terminate_instances(
    provider_name: str,
    cluster_name: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Terminate running or stopped instances."""
    raise NotImplementedError
