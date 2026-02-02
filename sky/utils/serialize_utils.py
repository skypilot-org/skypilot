"""Utilities for handling resource handles."""
import copy
import typing


def prepare_handle_for_backwards_compatibility(
        handle: typing.Any) -> typing.Any:
    """Prepare a handle for backwards compatibility with older clients."""
    # skylet_ssh_tunnel was causing backwards compatibility issues with older
    # clients: AttributeError: Can't get attribute 'SSHTunnelInfo'
    #
    # But it is not needed on the client side, so we can just remove it.
    if handle is not None and hasattr(handle, 'skylet_ssh_tunnel'):
        handle = copy.deepcopy(handle)
        handle.skylet_ssh_tunnel = None
    return handle


def prune_managed_job_handle(handle: typing.Any) -> typing.Any:
    """Prune a managed job handle for less data transfer."""
    if handle is None:
        return None
    handle = copy.deepcopy(handle)
    if hasattr(handle, 'skylet_ssh_tunnel'):
        handle.skylet_ssh_tunnel = None
    if (hasattr(handle, 'cached_cluster_info') and
            handle.cached_cluster_info is not None):
        handle.cached_cluster_info.provider_config = None
    return handle
