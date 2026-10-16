"""Shared GPU helpers for cloud image selection.

The default SkyPilot GPU image installs the NVIDIA open kernel module (see
sky/catalog/images/provisioners/cuda.sh). The open module depends on the GSP
firmware first introduced in Turing, so it only supports Turing and later
(T4, A100, L4, H100, B200, RTX PRO 6000, ...). Maxwell, Pascal (P100, P4) and
Volta (V100) are not supported and must keep using the older proprietary-driver
image. K80 is even older and uses its own dedicated image, so it is handled
separately by each cloud and is not listed here.
"""

# Pre-Turing GPUs that the open kernel module does not support. These must be
# launched with the legacy (proprietary driver / CUDA 12) image instead of the
# default image.
LEGACY_DRIVER_GPUS = frozenset({
    'M60',
    'P100',
    'P4',
    'V100',
})


def is_legacy_driver_gpu(accelerator_name: str) -> bool:
    """Returns True if the GPU needs the legacy proprietary-driver image."""
    return accelerator_name in LEGACY_DRIVER_GPUS
