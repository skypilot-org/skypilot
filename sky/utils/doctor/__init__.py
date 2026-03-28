"""sky doctor — accelerator health diagnostics plugin system.

Usage (programmatic)::

    from sky.utils.doctor.runner import run_doctor
    # ``record`` is a cluster status record with credentials included.
    success = run_doctor(record, verbose=True)

Plugin authors should subclass :class:`AcceleratorDoctorPlugin
<sky.utils.doctor.base.AcceleratorDoctorPlugin>` and register their class in
the ``_PLUGINS`` list in :mod:`sky.utils.doctor.runner`.
"""
from sky.utils.doctor.base import AcceleratorDoctorPlugin
from sky.utils.doctor.base import CheckResult
from sky.utils.doctor.base import CheckStatus
from sky.utils.doctor.base import RemoteRunner
from sky.utils.doctor.runner import run_doctor

__all__ = [
    'AcceleratorDoctorPlugin',
    'CheckResult',
    'CheckStatus',
    'RemoteRunner',
    'run_doctor',
]
