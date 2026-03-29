"""AMD ROCm GPU diagnostic plugin for ``sky doctor``.

This is a forward-looking stub that detects AMD GPUs via ``rocm-smi`` and
runs a small set of foundational checks.  Additional checks (ECC, bandwidth,
topology) can be added incrementally as the ROCm tooling matures.
"""
from typing import Callable, List

from sky.utils.doctor.base import AcceleratorDoctorPlugin
from sky.utils.doctor.base import CheckResult
from sky.utils.doctor.base import RemoteRunner


class AmdGpuDoctorPlugin(AcceleratorDoctorPlugin):
    """Runs AMD ROCm GPU health checks over SSH."""

    NAME = 'AMD GPU (ROCm)'
    ACCELERATOR_TYPE = 'amd'

    @classmethod
    def detect(cls, run_cmd: RemoteRunner) -> bool:
        rc, _, _ = run_cmd('command -v rocm-smi')
        return rc == 0

    # ----------------------------------------------------------------- checks

    def _check_rocm_smi(self) -> CheckResult:
        """Verify that rocm-smi is present and responds."""
        rc, out, err = self._run('rocm-smi --showproductname 2>&1')
        if rc != 0:
            return CheckResult.fail('rocm-smi',
                                    'rocm-smi failed or no AMD GPUs found.',
                                    details=(err or out).strip())
        gpu_ids = set()
        for line in out.splitlines():
            line = line.strip()
            lower = line.lower()
            if lower.startswith('gpu[') or lower.startswith('card['):
                gpu_ids.add(line.split(']')[0])
        summary = (f'{len(gpu_ids)} GPU(s) detected'
                   if gpu_ids else 'GPUs detected')
        return CheckResult.ok('rocm-smi', summary, details=out.strip()[:800])

    def _check_rocm_version(self) -> CheckResult:
        """Report the installed ROCm version."""
        _, out, _ = self._run(
            'cat /opt/rocm/.info/version 2>/dev/null || '
            'rocminfo 2>/dev/null | grep -i "ROCm Runtime" | head -1 || '
            'echo "__unknown"')
        if '__unknown' in out or not out.strip():
            return CheckResult.skipped('rocm-version',
                                       'Could not determine ROCm version.')
        version = out.strip().splitlines()[0].strip()
        return CheckResult.ok('rocm-version', f'ROCm version: {version}')

    def _check_gpu_memory(self) -> CheckResult:
        """Check GPU VRAM usage."""
        rc, out, _ = self._run('rocm-smi --showmeminfo vram 2>&1')
        if rc != 0 or not out.strip():
            return CheckResult.skipped('gpu-memory',
                                       'Could not retrieve VRAM info.')
        # Detailed parsing of VRAM usage left for future implementation.
        # rocm-smi reports lines like:
        #   GPU[0]  : VRAM Total Memory (B): 17163091968
        #   GPU[0]  : VRAM Total Used Memory (B): 1234567
        return CheckResult.ok('gpu-memory',
                              'VRAM info retrieved.',
                              details=out.strip()[:800])

    def _check_hip_env(self) -> CheckResult:
        """Check that HIP_VISIBLE_DEVICES / ROCR_VISIBLE_DEVICES are sane."""
        _, out, _ = self._run(
            'env | grep -iE "HIP_VISIBLE|ROCR_VISIBLE|GPU_DEVICE_ORDINAL" '
            '2>/dev/null || true')
        if not out.strip():
            return CheckResult.ok(
                'hip-env', 'No HIP/ROCR device visibility overrides set.')
        return CheckResult.ok('hip-env',
                              'HIP/ROCR device visibility variables set.',
                              details=out.strip())

    def checks(self) -> List[Callable[[], CheckResult]]:
        return [
            self._check_rocm_smi,
            self._check_rocm_version,
            self._check_gpu_memory,
            self._check_hip_env,
        ]
