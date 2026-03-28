"""NVIDIA GPU diagnostic plugin for ``sky doctor``."""
import re
import textwrap
from typing import Callable, Dict, List, Optional, Tuple

from sky.utils.doctor.base import AcceleratorDoctorPlugin
from sky.utils.doctor.base import CheckResult
from sky.utils.doctor.base import RemoteRunner

# ---------------------------------------------------------------------------
# CUDA driver / toolkit compatibility matrix.
# Source: https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html
# Maps minimum driver version (as integer) -> maximum supported CUDA version.
# ---------------------------------------------------------------------------
_DRIVER_TO_MAX_CUDA: List[Tuple[int, str]] = [
    # (min_driver_int, max_cuda_str)  — sorted descending by driver version
    (565, '12.7'),
    (560, '12.6'),
    (555, '12.5'),
    (550, '12.4'),
    (545, '12.3'),
    (535, '12.2'),
    (530, '12.1'),
    (525, '12.0'),
    (520, '11.8'),
    (515, '11.7'),
    (510, '11.6'),
    (495, '11.5'),
    (470, '11.4'),
    (460, '11.2'),
    (455, '11.1'),
    (450, '11.0'),
    (440, '10.2'),
    (418, '10.1'),
    (410, '10.0'),
    (396, '9.2'),
    (384, '9.1'),
    (375, '9.0'),
]

# NCCL environment variables that are known to cause issues when set to
# incorrect values.
_NCCL_WARN_VARS: Dict[str, str] = {
    'NCCL_SOCKET_IFNAME':
        ('Restricts NCCL to specific network interfaces. '
         'Can cause hangs if the specified interface is absent.'),
    'NCCL_IB_DISABLE': ('Setting to 1 disables InfiniBand / RoCE transport, '
                        'which may reduce bandwidth significantly.'),
    'NCCL_P2P_DISABLE':
        ('Setting to 1 disables peer-to-peer (NVLink / PCIe) transfers, '
         'which severely impacts multi-GPU communication.'),
    'NCCL_SHM_DISABLE': ('Setting to 1 disables shared-memory transport; '
                         'may force expensive fallback paths.'),
    'NCCL_DEBUG': ('Setting to INFO/WARN/TRACE enables verbose logging, '
                   'which can slow down communication.'),
}

# PCIe lane widths we consider degraded.
_PCIE_DEGRADED_WIDTHS = {'x1', 'x2', 'x4', 'x8'}


def _parse_driver_int(driver_str: str) -> Optional[int]:
    """Return the integer major version from a string like '535.104.05'."""
    m = re.match(r'^(\d+)', driver_str.strip())
    if m:
        return int(m.group(1))
    return None


def _cuda_from_driver(driver_major: int) -> Optional[str]:
    """Return the max CUDA version supported by the given driver major."""
    for min_drv, cuda_ver in _DRIVER_TO_MAX_CUDA:
        if driver_major >= min_drv:
            return cuda_ver
    return None


class NvidiaGpuDoctorPlugin(AcceleratorDoctorPlugin):
    """Runs NVIDIA GPU health checks over SSH."""

    NAME = 'NVIDIA GPU'
    ACCELERATOR_TYPE = 'nvidia'

    # ------------------------------------------------------------------ detect

    @classmethod
    def detect(cls, run_cmd: RemoteRunner) -> bool:
        rc, _, _ = run_cmd('command -v nvidia-smi')
        return rc == 0

    # ----------------------------------------------------------------- helpers

    def _smi(self, query: str, extra_flags: str = '') -> Tuple[int, str, str]:
        cmd = (f'nvidia-smi {extra_flags} --query-gpu={query}'
               ' --format=csv,noheader,nounits')
        return self._run(cmd)

    def _gpu_count(self) -> int:
        rc, out, _ = self._smi('name')
        if rc != 0:
            return 0
        return len([l for l in out.strip().splitlines() if l.strip()])

    # ---------------------------------------------------- individual checks

    def _check_nvidia_smi(self) -> CheckResult:
        """Verify that nvidia-smi is present and responds."""
        rc, out, err = self._run(
            'nvidia-smi --query-gpu=name --format=csv,noheader')
        if rc != 0:
            return CheckResult.fail('nvidia-smi',
                                    'nvidia-smi failed or no GPUs found.',
                                    details=err.strip() or out.strip())
        gpus = [l.strip() for l in out.strip().splitlines() if l.strip()]
        return CheckResult.ok(
            'nvidia-smi', f'{len(gpus)} GPU(s) detected: {", ".join(gpus)}')

    def _check_driver_cuda_compat(self) -> CheckResult:
        """Check driver version vs installed CUDA toolkit compatibility."""
        rc, out, _ = self._smi('driver_version')
        if rc != 0 or not out.strip():
            return CheckResult.skipped('driver-cuda-compat',
                                       'Could not retrieve driver version.')
        driver_str = out.strip().splitlines()[0].strip()
        driver_major = _parse_driver_int(driver_str)
        if driver_major is None:
            return CheckResult.warn(
                'driver-cuda-compat',
                f'Could not parse driver version: {driver_str!r}')

        max_cuda = _cuda_from_driver(driver_major)

        # Try to detect installed CUDA toolkit version.
        rc2, nvcc_out, _ = self._run('nvcc --version 2>/dev/null || true')
        cuda_toolkit: Optional[str] = None
        if rc2 == 0:
            m = re.search(r'release (\d+\.\d+)', nvcc_out)
            if m:
                cuda_toolkit = m.group(1)

        detail_lines = [f'Driver: {driver_str}']
        if cuda_toolkit:
            detail_lines.append(f'CUDA toolkit (nvcc): {cuda_toolkit}')
        if max_cuda:
            detail_lines.append(f'Max CUDA supported by driver: {max_cuda}')

        details = '\n'.join(detail_lines)

        if max_cuda is None:
            return CheckResult.warn(
                'driver-cuda-compat',
                f'Driver {driver_str} is below CUDA 9.0 minimum.',
                details=details)

        if cuda_toolkit:
            # Compare major.minor
            def _ver_tuple(v: str) -> Tuple[int, int]:
                parts = v.split('.')
                return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0

            if _ver_tuple(cuda_toolkit) > _ver_tuple(max_cuda):
                return CheckResult.fail(
                    'driver-cuda-compat',
                    f'CUDA toolkit {cuda_toolkit} requires a newer driver '
                    f'(current: {driver_str}, supports up to CUDA {max_cuda}).',
                    details=details)

        return CheckResult.ok(
            'driver-cuda-compat',
            f'Driver {driver_str} compatible (supports up to CUDA {max_cuda}).',
            details=details)

    def _check_ecc_errors(self) -> CheckResult:
        """Check for ECC memory errors (single-bit and double-bit)."""
        rc, out, _ = self._smi('ecc.errors.corrected.volatile.total,'
                               'ecc.errors.uncorrected.volatile.total,'
                               'ecc.errors.corrected.aggregate.total,'
                               'ecc.errors.uncorrected.aggregate.total,'
                               'ecc.mode.current')
        if rc != 0:
            return CheckResult.skipped('ecc-errors',
                                       'ECC query not supported on this GPU.')

        lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
        if not lines:
            return CheckResult.skipped('ecc-errors', 'No ECC data available.')

        failed_gpus = []
        warned_gpus = []
        for idx, line in enumerate(lines):
            parts = [p.strip() for p in line.split(',')]
            if len(parts) < 5:
                continue
            sbe_vol, dbe_vol, _, dbe_agg, mode = parts[:5]
            if mode.lower() in ('disabled', 'n/a', '[n/a]'):
                continue
            try:
                dbe_v = int(dbe_vol) if dbe_vol not in ('N/A', '[N/A]') else 0
                dbe_a = int(dbe_agg) if dbe_agg not in ('N/A', '[N/A]') else 0
                sbe_v = int(sbe_vol) if sbe_vol not in ('N/A', '[N/A]') else 0
            except ValueError:
                continue
            if dbe_v > 0 or dbe_a > 0:
                failed_gpus.append(f'GPU {idx}: {dbe_v} uncorrected volatile, '
                                   f'{dbe_a} uncorrected aggregate DBEs')
            elif sbe_v > 0:
                warned_gpus.append(
                    f'GPU {idx}: {sbe_v} corrected single-bit errors '
                    '(volatile)')

        if failed_gpus:
            return CheckResult.fail(
                'ecc-errors',
                f'{len(failed_gpus)} GPU(s) have uncorrected ECC errors.',
                details='\n'.join(failed_gpus))
        if warned_gpus:
            return CheckResult.warn(
                'ecc-errors',
                f'{len(warned_gpus)} GPU(s) have corrected single-bit errors.',
                details='\n'.join(warned_gpus))
        return CheckResult.ok('ecc-errors', 'No ECC errors detected.')

    def _check_memory_fragmentation(self) -> CheckResult:
        """Check GPU memory free vs total to detect fragmentation / leaks."""
        rc, out, _ = self._smi('memory.used,memory.free,memory.total')
        if rc != 0 or not out.strip():
            return CheckResult.skipped('gpu-memory',
                                       'Could not retrieve memory info.')

        lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
        warnings = []
        details_lines = []
        for idx, line in enumerate(lines):
            parts = [p.strip() for p in line.split(',')]
            if len(parts) < 3:
                continue
            try:
                used = int(parts[0])
                free = int(parts[1])
                total = int(parts[2])
            except ValueError:
                continue
            used_pct = (used / total * 100) if total else 0
            details_lines.append(
                f'GPU {idx}: {used} MiB used / {total} MiB total '
                f'({used_pct:.1f}% used, {free} MiB free)')
            if used_pct > 90:
                warnings.append(f'GPU {idx}: memory usage {used_pct:.1f}% '
                                f'({used}/{total} MiB)')

        details = '\n'.join(details_lines)
        if warnings:
            return CheckResult.warn(
                'gpu-memory',
                f'{len(warnings)} GPU(s) have >90% memory utilization.',
                details=details)
        return CheckResult.ok('gpu-memory',
                              'GPU memory utilization within normal range.',
                              details=details)

    def _check_nvlink(self) -> CheckResult:
        """Inspect NVLink topology and link status."""
        rc, out, _ = self._run(
            'nvidia-smi nvlink --status 2>/dev/null || echo "__not_supported"')
        if '__not_supported' in out or rc != 0:
            return CheckResult.skipped('nvlink',
                                       'NVLink not available on this system.')

        if 'Inactive' in out or 'inactive' in out:
            inactive = [
                l.strip() for l in out.splitlines() if 'nactive' in l.lower()
            ]
            return CheckResult.warn(
                'nvlink',
                f'{len(inactive)} inactive NVLink link(s) found.',
                details='\n'.join(inactive[:20]))

        # Count active links.
        active = [l.strip() for l in out.splitlines() if 'Active' in l]
        return CheckResult.ok('nvlink',
                              f'{len(active)} active NVLink link(s).',
                              details=out.strip()[:1000] if active else None)

    def _check_pcie_bandwidth(self) -> CheckResult:
        """Validate PCIe link width — degraded widths indicate slot issues."""
        rc, out, _ = self._smi('pcie.link.width.current')
        if rc != 0 or not out.strip():
            return CheckResult.skipped('pcie-width',
                                       'Could not query PCIe link width.')

        lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
        degraded = []
        details_lines = []
        for idx, width_str in enumerate(lines):
            width_str = width_str.strip()
            if not width_str or width_str in ('N/A', '[N/A]'):
                continue
            # Normalise: some drivers return '16', others 'x16'.
            if not width_str.startswith('x'):
                width_str = f'x{width_str}'
            details_lines.append(f'GPU {idx}: PCIe link width {width_str}')
            if width_str in _PCIE_DEGRADED_WIDTHS:
                degraded.append(f'GPU {idx}: PCIe link width {width_str} '
                                '(expected x16 for full bandwidth)')

        details = '\n'.join(details_lines)
        if degraded:
            return CheckResult.warn(
                'pcie-width',
                f'{len(degraded)} GPU(s) running at degraded PCIe width.',
                details='\n'.join(degraded))
        return CheckResult.ok('pcie-width',
                              'PCIe link widths are nominal.',
                              details=details if details else None)

    def _check_nccl_env(self) -> CheckResult:
        """Flag NCCL environment variables that are known to cause issues."""
        rc, out, _ = self._run('env | grep -i nccl 2>/dev/null || true')
        if rc != 0:
            return CheckResult.skipped('nccl-env',
                                       'Could not read environment.')

        set_vars: Dict[str, str] = {}
        for line in out.strip().splitlines():
            if '=' in line:
                k, _, v = line.partition('=')
                set_vars[k.strip().upper()] = v.strip()

        problematic = []
        for var, explanation in _NCCL_WARN_VARS.items():
            if var in set_vars:
                problematic.append(f'{var}={set_vars[var]!r}  — {explanation}')

        if not set_vars:
            return CheckResult.ok('nccl-env',
                                  'No NCCL environment variables set.')
        if problematic:
            return CheckResult.warn(
                'nccl-env', f'{len(problematic)} potentially problematic NCCL '
                'variable(s) set.',
                details='\n'.join(problematic))
        all_vars = ', '.join(f'{k}={v}' for k, v in set_vars.items())
        return CheckResult.ok('nccl-env',
                              'NCCL env vars present but none flagged.',
                              details=all_vars)

    def _check_gpu_clocks(self) -> CheckResult:
        """Detect GPUs in a throttled or low-power clock state."""
        rc, out, _ = self._smi('clocks_throttle_reasons.active')
        if rc != 0 or not out.strip():
            return CheckResult.skipped('gpu-clocks',
                                       'Clock throttle query not supported.')

        lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
        throttled = []
        for idx, reason_str in enumerate(lines):
            # The field is a bitmask; '0x0000000000000000' means none.
            reason_str = reason_str.strip()
            if reason_str in ('0x0000000000000000', '0', 'Not Active', 'N/A',
                              '[N/A]'):
                continue
            throttled.append(
                f'GPU {idx}: throttle reasons active = {reason_str}')

        if throttled:
            return CheckResult.warn(
                'gpu-clocks',
                f'{len(throttled)} GPU(s) have active clock throttle reasons.',
                details=textwrap.dedent("""\
                    Common causes: thermal throttle, power limit, SW thermal slowdown.
                    """) + '\n'.join(throttled))
        return CheckResult.ok('gpu-clocks', 'No GPU clock throttle detected.')

    # ------------------------------------------------------- plugin interface

    def checks(self) -> List[Callable[[], CheckResult]]:
        return [
            self._check_nvidia_smi,
            self._check_driver_cuda_compat,
            self._check_ecc_errors,
            self._check_memory_fragmentation,
            self._check_nvlink,
            self._check_pcie_bandwidth,
            self._check_nccl_env,
            self._check_gpu_clocks,
        ]
