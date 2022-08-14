"""Resources related utilities."""
from sky.clouds import service_catalog
from sky.utils import ux_utils

# List of all accelerators supported by SkyPilot.
# NOTE: Must include accelerators supported for local clusters.
_ACCELERATORS = [
    'A100',
    'A10G',
    'Gaudi HL-205',
    'Inferentia',
    'K520',
    'K80',
    'M60',
    'Radeon Pro V520',
    'T4',
    'T4g',
    'V100',
    'V100-32GB',
    'Virtex UltraScale (VU9P)',
    'A10',
    'A100-80GB',
    'P100',
    'P40',
    'Radeon MI25',
    'P4',
]


def canonicalize_accelerator_name(accelerator: str) -> str:
    """Returns the canonical accelerator name."""
    # TPU names are always lowercase.
    if accelerator.lower().startswith('tpu-'):
        return accelerator.lower()

    mapping = {name.lower(): name for name in _ACCELERATORS}
    if accelerator.lower() in mapping:
        return mapping[accelerator.lower()]

    # A new accelerator can be added after upgrading SkyPilot.
    # Search the accelerator name in the service catalog.
    searched = service_catalog.list_accelerators(
        name_filter=accelerator, case_sensitive=False)
    names = list(searched.keys())

    # Exact match.
    if accelerator in names:
        return accelerator

    if len(names) == 0:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Unknown accelerator: {accelerator}')

    if len(names) == 1:
        return names[0]

    if len(names) > 1:
        with ux_utils.print_exception_no_traceback():
            raise ValueError(f'Accelerator name {accelerator} is ambiguous. '
                            f'Please choose one of {names}.')

# TODO(woosuk): canonicalize the Azure instance type names.
