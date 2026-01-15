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
