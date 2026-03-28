"""Google Cloud TPU diagnostic plugin for ``sky doctor``.

This is a forward-looking stub that detects TPU runtimes and runs basic
topology / runtime checks.  Deeper checks (cross-chip interconnect health,
XLA runtime validation) can be added incrementally.
"""
from typing import Callable, List

from sky.utils.doctor.base import AcceleratorDoctorPlugin
from sky.utils.doctor.base import CheckResult
from sky.utils.doctor.base import RemoteRunner


class TpuDoctorPlugin(AcceleratorDoctorPlugin):
    """Runs Google Cloud TPU health checks over SSH."""

    NAME = 'Google Cloud TPU'
    ACCELERATOR_TYPE = 'tpu'

    @classmethod
    def detect(cls, run_cmd: RemoteRunner) -> bool:
        # The presence of /dev/accel0 or the `libtpu` package indicates a TPU
        # VM.  We also check for `tpu-info` which ships in recent TPU runtimes.
        rc_accel, _, _ = run_cmd('test -e /dev/accel0')
        if rc_accel == 0:
            return True
        rc_tpu, _, _ = run_cmd('command -v tpu-info')
        return rc_tpu == 0

    # ----------------------------------------------------------------- checks

    def _check_tpu_runtime(self) -> CheckResult:
        """Check TPU runtime availability."""
        _, out, _ = self._run(
            'tpu-info 2>&1 || ls /dev/accel* 2>&1 || echo "__no_tpu"')
        if '__no_tpu' in out:
            return CheckResult.fail('tpu-runtime',
                                    'No TPU runtime or devices found.')
        return CheckResult.ok('tpu-runtime',
                              'TPU runtime / devices detected.',
                              details=out.strip()[:800])

    def _check_libtpu(self) -> CheckResult:
        """Verify libtpu is installed."""
        rc, out, _ = self._run(
            'python3 -c "import libtpu; print(libtpu.__version__)" '
            '2>/dev/null || echo "__missing"')
        if '__missing' in out or rc != 0:
            return CheckResult.warn(
                'libtpu', 'libtpu Python package not found or import failed.')
        version = out.strip().splitlines()[-1].strip()
        return CheckResult.ok('libtpu', f'libtpu version: {version}')

    def _check_tpu_topology(self) -> CheckResult:
        """Report TPU topology (chip count, type)."""
        tpu_count_cmd = ('python3 -c "'
                         'import os; '
                         'chips = len([f for f in os.listdir("/dev") '
                         'if f.startswith("accel")]); '
                         'print(f\"{chips} TPU chip(s) found\")"'
                         ' 2>/dev/null || echo "__unknown"')
        rc, out, _ = self._run(tpu_count_cmd)
        if '__unknown' in out or rc != 0:
            return CheckResult.skipped('tpu-topology',
                                       'Could not determine TPU topology.')
        summary = out.strip().splitlines()[-1].strip()
        return CheckResult.ok('tpu-topology', summary)

    def _check_tpu_env(self) -> CheckResult:
        """Check relevant TPU environment variables."""
        _, out, _ = self._run(
            'env | grep -iE "TPU_|XLA_|LIBTPU_" 2>/dev/null || true')
        if not out.strip():
            return CheckResult.ok('tpu-env', 'No TPU/XLA env overrides set.')
        return CheckResult.ok('tpu-env',
                              'TPU/XLA environment variables set.',
                              details=out.strip()[:800])

    def checks(self) -> List[Callable[[], CheckResult]]:
        return [
            self._check_tpu_runtime,
            self._check_libtpu,
            self._check_tpu_topology,
            self._check_tpu_env,
        ]
