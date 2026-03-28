"""Unit tests for sky/utils/doctor/ accelerator health check plugins."""
import pytest

from sky.utils.doctor.amd_gpu import AmdGpuDoctorPlugin
from sky.utils.doctor.base import CheckResult
from sky.utils.doctor.base import CheckStatus
from sky.utils.doctor.nvidia_gpu import _cuda_from_driver
from sky.utils.doctor.nvidia_gpu import _parse_driver_int
from sky.utils.doctor.nvidia_gpu import NvidiaGpuDoctorPlugin
from sky.utils.doctor.runner import _detect_plugin
from sky.utils.doctor.tpu import TpuDoctorPlugin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner(responses: dict):
    """Return a fake RemoteRunner backed by a cmd->output dict.

    Each key in *responses* is matched against the beginning of the command
    string.  The value is a (returncode, stdout, stderr) tuple.
    If no match is found, returns (1, '', 'command not found').
    """

    def _run(cmd: str):
        for prefix, result in responses.items():
            if prefix in cmd:
                return result
        return 1, '', 'command not found'

    return _run


# ---------------------------------------------------------------------------
# CheckResult helpers
# ---------------------------------------------------------------------------


class TestCheckResult:

    def test_ok_factory(self):
        r = CheckResult.ok('test', 'all good')
        assert r.status == CheckStatus.OK
        assert r.name == 'test'
        assert r.message == 'all good'
        assert r.details is None

    def test_warn_factory(self):
        r = CheckResult.warn('test', 'watch out', details='extra info')
        assert r.status == CheckStatus.WARNING
        assert r.details == 'extra info'

    def test_fail_factory(self):
        r = CheckResult.fail('test', 'broken')
        assert r.status == CheckStatus.FAIL

    def test_skipped_factory(self):
        r = CheckResult.skipped('test', 'not applicable')
        assert r.status == CheckStatus.SKIPPED
        assert r.details is None


# ---------------------------------------------------------------------------
# Driver/CUDA compat helpers
# ---------------------------------------------------------------------------


class TestDriverCudaHelpers:

    @pytest.mark.parametrize('driver_str,expected', [
        ('535.104.05', 535),
        ('470', 470),
        ('520.61.05', 520),
        ('notadriver', None),
        ('', None),
    ])
    def test_parse_driver_int(self, driver_str, expected):
        assert _parse_driver_int(driver_str) == expected

    @pytest.mark.parametrize(
        'driver_major,expected_cuda',
        [
            (565, '12.7'),
            (560, '12.6'),
            (535, '12.2'),
            (470, '11.4'),
            (384, '9.1'),
            (100, None),  # below minimum
        ])
    def test_cuda_from_driver(self, driver_major, expected_cuda):
        assert _cuda_from_driver(driver_major) == expected_cuda


# ---------------------------------------------------------------------------
# NvidiaGpuDoctorPlugin — detect
# ---------------------------------------------------------------------------


class TestNvidiaGpuDetect:

    def test_detect_true_when_nvidia_smi_present(self):
        runner = _make_runner(
            {'command -v nvidia-smi': (0, '/usr/bin/nvidia-smi', '')})
        assert NvidiaGpuDoctorPlugin.detect(runner) is True

    def test_detect_false_when_nvidia_smi_absent(self):
        runner = _make_runner({})
        assert NvidiaGpuDoctorPlugin.detect(runner) is False


# ---------------------------------------------------------------------------
# NvidiaGpuDoctorPlugin — individual checks
# ---------------------------------------------------------------------------


class TestNvidiaGpuChecks:

    def _plugin(self, responses):
        runner = _make_runner(responses)
        return NvidiaGpuDoctorPlugin(runner)

    def test_nvidia_smi_ok(self):
        p = self._plugin({
            '--query-gpu=name': (0, 'Tesla V100\nTesla V100\n', ''),
        })
        result = p._check_nvidia_smi()
        assert result.status == CheckStatus.OK
        assert '2 GPU(s)' in result.message

    def test_nvidia_smi_fail(self):
        p = self._plugin({'--query-gpu=name': (1, '', 'NVML error')})
        result = p._check_nvidia_smi()
        assert result.status == CheckStatus.FAIL

    def test_driver_cuda_compat_ok(self):
        # Driver 535 supports up to CUDA 12.2; toolkit 12.1 is fine.
        p = self._plugin({
            'driver_version': (0, '535.104.05\n', ''),
            'nvcc --version':
                (0, 'Cuda compilation tools, release 12.1, V12.1.66', ''),
        })
        result = p._check_driver_cuda_compat()
        assert result.status == CheckStatus.OK

    def test_driver_cuda_compat_fail_toolkit_too_new(self):
        # Driver 470 supports up to CUDA 11.4; toolkit 12.0 should fail.
        p = self._plugin({
            'driver_version': (0, '470.57.02\n', ''),
            'nvcc --version':
                (0, 'Cuda compilation tools, release 12.0, V12.0.0', ''),
        })
        result = p._check_driver_cuda_compat()
        assert result.status == CheckStatus.FAIL

    def test_driver_cuda_compat_no_toolkit(self):
        # No nvcc — should still pass as long as driver is modern.
        p = self._plugin({
            'driver_version': (0, '535.104.05\n', ''),
            'nvcc --version': (1, '', 'nvcc: command not found'),
        })
        result = p._check_driver_cuda_compat()
        assert result.status == CheckStatus.OK

    def test_ecc_no_errors(self):
        # 0 SBE, 0 DBE, ECC enabled
        smi_out = '0, 0, 0, 0, Enabled\n0, 0, 0, 0, Enabled\n'
        p = self._plugin({'ecc.errors': (0, smi_out, '')})
        result = p._check_ecc_errors()
        assert result.status == CheckStatus.OK

    def test_ecc_double_bit_error(self):
        smi_out = '0, 2, 0, 5, Enabled\n'
        p = self._plugin({'ecc.errors': (0, smi_out, '')})
        result = p._check_ecc_errors()
        assert result.status == CheckStatus.FAIL

    def test_ecc_single_bit_warning_volatile(self):
        # Volatile SBE only
        smi_out = '3, 0, 0, 0, Enabled\n'
        p = self._plugin({'ecc.errors': (0, smi_out, '')})
        result = p._check_ecc_errors()
        assert result.status == CheckStatus.WARNING
        assert 'volatile' in result.details

    def test_ecc_single_bit_warning_aggregate(self):
        # Aggregate SBE only (no volatile)
        smi_out = '0, 0, 10, 0, Enabled\n'
        p = self._plugin({'ecc.errors': (0, smi_out, '')})
        result = p._check_ecc_errors()
        assert result.status == CheckStatus.WARNING
        assert 'aggregate' in result.details

    def test_ecc_disabled(self):
        smi_out = '0, 0, 0, 0, Disabled\n'
        p = self._plugin({'ecc.errors': (0, smi_out, '')})
        result = p._check_ecc_errors()
        assert result.status == CheckStatus.OK

    def test_memory_ok(self):
        # 50% used
        p = self._plugin({'memory.used': (0, '8000, 8000, 16000\n', '')})
        result = p._check_memory_fragmentation()
        assert result.status == CheckStatus.OK

    def test_memory_high_utilization(self):
        # 95% used
        p = self._plugin({'memory.used': (0, '15200, 800, 16000\n', '')})
        result = p._check_memory_fragmentation()
        assert result.status == CheckStatus.WARNING

    def test_nvlink_not_supported(self):
        p = self._plugin({'nvlink': (0, '__not_supported', '')})
        result = p._check_nvlink()
        assert result.status == CheckStatus.SKIPPED

    def test_nvlink_active(self):
        p = self._plugin(
            {'nvlink': (0, 'Link 0: Active\nLink 1: Active\n', '')})
        result = p._check_nvlink()
        assert result.status == CheckStatus.OK

    def test_nvlink_inactive(self):
        p = self._plugin(
            {'nvlink': (0, 'Link 0: Active\nLink 1: Inactive\n', '')})
        result = p._check_nvlink()
        assert result.status == CheckStatus.WARNING

    def test_pcie_width_full(self):
        p = self._plugin({'pcie.link.width.current': (0, '16\n16\n', '')})
        result = p._check_pcie_bandwidth()
        assert result.status == CheckStatus.OK

    def test_pcie_width_degraded(self):
        p = self._plugin({'pcie.link.width.current': (0, '16\n8\n', '')})
        result = p._check_pcie_bandwidth()
        assert result.status == CheckStatus.WARNING

    def test_nccl_env_no_vars(self):
        p = self._plugin({'env | grep -i nccl': (0, '', '')})
        result = p._check_nccl_env()
        assert result.status == CheckStatus.OK

    def test_nccl_env_problematic(self):
        p = self._plugin({
            'env | grep -i nccl':
                (0, 'NCCL_P2P_DISABLE=1\nNCCL_IB_DISABLE=1\n', '')
        })
        result = p._check_nccl_env()
        assert result.status == CheckStatus.WARNING
        assert '2' in result.message

    def test_clock_throttle_none(self):
        p = self._plugin(
            {'clocks_throttle_reasons': (0, '0x0000000000000000\n', '')})
        result = p._check_gpu_clocks()
        assert result.status == CheckStatus.OK

    def test_clock_throttle_active(self):
        p = self._plugin(
            {'clocks_throttle_reasons': (0, '0x0000000000000008\n', '')})
        result = p._check_gpu_clocks()
        assert result.status == CheckStatus.WARNING

    def test_checks_returns_all_callables(self):
        p = self._plugin({})
        checks = p.checks()
        assert len(checks) == 8
        assert all(callable(c) for c in checks)


# ---------------------------------------------------------------------------
# AmdGpuDoctorPlugin — detect
# ---------------------------------------------------------------------------


class TestAmdGpuDetect:

    def test_detect_true(self):
        runner = _make_runner(
            {'command -v rocm-smi': (0, '/usr/bin/rocm-smi', '')})
        assert AmdGpuDoctorPlugin.detect(runner) is True

    def test_detect_false(self):
        runner = _make_runner({})
        assert AmdGpuDoctorPlugin.detect(runner) is False


# ---------------------------------------------------------------------------
# TpuDoctorPlugin — detect
# ---------------------------------------------------------------------------


class TestTpuDetect:

    def test_detect_via_accel_device(self):
        runner = _make_runner({'test -e /dev/accel0': (0, '', '')})
        assert TpuDoctorPlugin.detect(runner) is True

    def test_detect_via_tpu_info(self):
        runner = _make_runner({
            'test -e /dev/accel0': (1, '', ''),
            'command -v tpu-info': (0, '/usr/bin/tpu-info', ''),
        })
        assert TpuDoctorPlugin.detect(runner) is True

    def test_detect_false(self):
        runner = _make_runner({})
        assert TpuDoctorPlugin.detect(runner) is False


# ---------------------------------------------------------------------------
# Auto-detection in runner
# ---------------------------------------------------------------------------


class TestDetectPlugin:

    def test_detects_nvidia(self):
        runner = _make_runner({'command -v nvidia-smi': (0, '', '')})
        plugin = _detect_plugin(runner)
        assert isinstance(plugin, NvidiaGpuDoctorPlugin)

    def test_detects_amd(self):
        runner = _make_runner({'command -v rocm-smi': (0, '', '')})
        plugin = _detect_plugin(runner)
        assert isinstance(plugin, AmdGpuDoctorPlugin)

    def test_detects_tpu(self):
        runner = _make_runner({'test -e /dev/accel0': (0, '', '')})
        plugin = _detect_plugin(runner)
        assert isinstance(plugin, TpuDoctorPlugin)

    def test_nvidia_takes_priority_over_amd(self):
        # If somehow both are present, NVIDIA wins (first in _PLUGINS list).
        runner = _make_runner({
            'command -v nvidia-smi': (0, '', ''),
            'command -v rocm-smi': (0, '', ''),
        })
        plugin = _detect_plugin(runner)
        assert isinstance(plugin, NvidiaGpuDoctorPlugin)

    def test_returns_none_when_no_accelerator(self):
        runner = _make_runner({})
        plugin = _detect_plugin(runner)
        assert plugin is None
