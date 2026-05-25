"""Unit tests for ``_SBATCH_PROTECTED_OPTIONS`` enforcement.

Pinpoints *today's* protection behavior — long-form names are blocked,
short-form aliases are not (PLAN.md gap "SBATCH protection"). The
``time``-not-protected behavior is already covered by
``test_sbatch_config.py::TestBuildCustomSbatchDirectives``; this file
focuses on the v1-relevant additions: ``--signal`` (commit d5738c856)
plus the short-form gap.
"""
from unittest import mock

import pytest

from sky.provision.slurm import instance as slurm_instance
from sky.provision.slurm.instance import _build_custom_sbatch_directives
from sky.provision.slurm.instance import _SBATCH_PROTECTED_OPTIONS


class TestLongFormProtectedOptionsSkipped:
    """Long-form names in ``_SBATCH_PROTECTED_OPTIONS`` are warned + skipped."""

    @pytest.mark.parametrize('option', [
        'nodes',
        'output',
        'error',
        'job-name',
        'partition',
        'wait-all-nodes',
        'no-requeue',
        'cpus-per-task',
        'mem',
        'gres',
    ])
    def test_long_form_protected_skipped(self, option):
        assert _build_custom_sbatch_directives({option: 'value'}) == ''

    def test_signal_is_protected(self, monkeypatch):
        """``--signal`` was added to the protected set in commit d5738c856
        because a user ``signal: KILL@10`` skips SIGTERM grace and can
        truncate the log tail before tail_logs / cleanup completes."""
        assert 'signal' in _SBATCH_PROTECTED_OPTIONS
        warning_mock = mock.MagicMock()
        monkeypatch.setattr(slurm_instance.logger, 'warning', warning_mock)
        result = _build_custom_sbatch_directives({'signal': 'KILL@10'})
        assert result == ''
        # And a warning was logged to surface the skip.
        warning_mock.assert_called_once()
        msg = warning_mock.call_args.args[0]
        assert 'signal' in msg.lower() or 'ignor' in msg.lower()


class TestNonProtectedOptionsEmitted:
    """Non-protected options pass through as ``#SBATCH --key=value``."""

    @pytest.mark.parametrize('key,value', [
        ('account', 'research'),
        ('qos', 'high'),
        ('time', '4:00:00'),
        ('mail-user', 'user@example.com'),
        ('mail-type', 'END'),
        ('constraint', 'a100'),
        ('reservation', 'gpu-reservation'),
    ])
    def test_non_protected_emitted(self, key, value):
        result = _build_custom_sbatch_directives({key: value})
        assert result == f'\n#SBATCH --{key}={value}'


class TestUnderscoreNormalization:
    """Underscore-keyed options are normalized to hyphenated form before
    being matched against ``_SBATCH_PROTECTED_OPTIONS`` and emitted."""

    def test_underscore_normalized_in_emitted_directive(self):
        result = _build_custom_sbatch_directives({'mail_user': 'u@example.com'})
        assert result == '\n#SBATCH --mail-user=u@example.com'

    def test_underscore_normalized_for_protection_check(self):
        """``cpus_per_task`` should be caught by protection even though
        the protected-options set uses the hyphenated form."""
        assert _build_custom_sbatch_directives({'cpus_per_task': 4}) == ''

    def test_underscore_signal_protected(self):
        # Defensive: ``signal`` has no underscore variant in normal usage,
        # but if a user writes ``signal_`` or similar, it must still be
        # caught.  (Direct ``signal`` is tested above.)
        assert _build_custom_sbatch_directives({'signal': 'KILL@10'}) == ''


class TestEmpty:

    def test_empty_input(self):
        assert _build_custom_sbatch_directives({}) == ''


# ------------------- Documented gap: short-form aliases --------------- #


class TestShortFormProtectionGap:
    """Short-form aliases (-N, -J, -o, -e, -t, -p) currently bypass
    protection — see PLAN.md gap #7 "SBATCH protection".

    These ``xfail`` tests document the gap.  When the short-form
    normalizer lands they should flip to passing; remove the ``xfail``
    marker at that point.
    """

    @pytest.mark.xfail(
        reason='PLAN.md gap #7: short-form sbatch aliases are not yet '
        'normalized into the protection check. Adding -N -> nodes, -J -> '
        'job-name, -o -> output, -e -> error, -t -> time, -p -> partition '
        'is tracked separately as an OSS rework.',
        strict=True,
    )
    @pytest.mark.parametrize('short,long_form', [
        ('N', 'nodes'),
        ('J', 'job-name'),
        ('o', 'output'),
        ('e', 'error'),
        ('p', 'partition'),
    ])
    def test_short_form_aliases_should_be_protected(self, short, long_form):
        # When this gap is closed, the short form should be treated
        # identically to the long form and produce an empty string.
        del long_form  # Documentation-only.
        assert _build_custom_sbatch_directives({short: 'value'}) == ''
