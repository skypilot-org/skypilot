"""Unit tests for sbatch_options support in Slurm provisioner."""

import subprocess
import unittest.mock as mock

import jsonschema
import pytest

from sky.adaptors import slurm as slurm_adaptor
from sky.provision.slurm import instance as slurm_instance
from sky.provision.slurm.instance import _build_custom_sbatch_directives
from sky.provision.slurm.instance import _build_sbatch_directives
from sky.provision.slurm.instance import _compute_time_directive
from sky.provision.slurm.instance import _SBATCH_PROTECTED_OPTIONS
from sky.utils.schemas import get_config_schema


class TestBuildCustomSbatchDirectives:
    """Test _build_custom_sbatch_directives()."""

    def test_empty_config(self):
        assert _build_custom_sbatch_directives({}) == ''

    def test_string_value(self):
        result = _build_custom_sbatch_directives({'qos': 'high'})
        assert result == '\n#SBATCH --qos=high'

    def test_numeric_value(self):
        result = _build_custom_sbatch_directives({'nice': 100})
        assert result == '\n#SBATCH --nice=100'

    def test_boolean_true_flag(self):
        result = _build_custom_sbatch_directives({'exclusive': True})
        assert result == '\n#SBATCH --exclusive'

    def test_boolean_false_omitted(self):
        result = _build_custom_sbatch_directives({'exclusive': False})
        assert result == ''

    def test_null_omitted(self):
        result = _build_custom_sbatch_directives({'qos': None})
        assert result == ''

    def test_multiple_options_sorted(self):
        result = _build_custom_sbatch_directives({
            'qos': 'high',
            'account': 'research',
            'constraint': 'skylake',
        })
        # Leading newline for f-string insertion, then sorted directives.
        lines = result.strip().split('\n')
        assert lines == [
            '#SBATCH --account=research',
            '#SBATCH --constraint=skylake',
            '#SBATCH --qos=high',
        ]

    def test_mixed_values(self):
        result = _build_custom_sbatch_directives({
            'qos': 'normal',
            'exclusive': True,
            'nice': 50,
            'comment': None,
        })
        lines = result.strip().split('\n')
        assert '#SBATCH --exclusive' in lines
        assert '#SBATCH --nice=50' in lines
        assert '#SBATCH --qos=normal' in lines
        assert len(lines) == 3  # None should be omitted

    def test_underscore_normalized_to_hyphen(self):
        result = _build_custom_sbatch_directives({'mail_type': 'END'})
        assert result == '\n#SBATCH --mail-type=END'

    def test_protected_option_skipped(self):
        result = _build_custom_sbatch_directives({'job-name': 'my-job'})
        assert result == ''

    def test_protected_option_with_underscore_skipped(self):
        result = _build_custom_sbatch_directives({'cpus_per_task': 4})
        assert result == ''

    def test_protected_option_partition_skipped(self):
        result = _build_custom_sbatch_directives({'partition': 'gpu'})
        assert result == ''

    @pytest.mark.parametrize('option', sorted(_SBATCH_PROTECTED_OPTIONS))
    def test_all_protected_options_skipped(self, option):
        result = _build_custom_sbatch_directives({option: 'value'})
        assert result == ''

    def test_protected_options_skipped_non_protected_kept(self):
        result = _build_custom_sbatch_directives({
            'job-name': 'my-job',
            'qos': 'high',
            'nodes': 4,
        })
        assert result == '\n#SBATCH --qos=high'

    def test_newline_in_value_raises(self):
        with pytest.raises(ValueError, match='Newline'):
            _build_custom_sbatch_directives({'comment': 'foo\nbar'})

    def test_newline_in_key_raises(self):
        with pytest.raises(ValueError, match='Newline'):
            _build_custom_sbatch_directives({'foo\nbar': 'value'})

    @pytest.mark.parametrize('value', [
        'foo; rm -rf /',
        'foo && echo pwned',
        'foo | cat /etc/passwd',
        '$(whoami)',
        '`whoami`',
        'foo > /tmp/out',
        'foo < /dev/null',
        'foo\' || echo pwned #',
    ])
    def test_shell_metacharacters_safe_in_comment(self, value):
        """Shell metacharacters are safe because #SBATCH lines are comments.

        Verify by actually executing the directive as a bash script and
        checking that only the sentinel value is produced (i.e. the
        metacharacters in the comment were not executed).
        """
        result = _build_custom_sbatch_directives({'comment': value})
        assert f'#SBATCH --comment={value}' in result
        # Run the directive + a sentinel echo as a real bash script.
        script = result.strip() + '\necho SAFE'
        proc = subprocess.run(['bash', '-c', script],
                              capture_output=True,
                              text=True,
                              check=False)
        assert proc.stdout.strip() == 'SAFE'

    def test_non_protected_options_allowed(self):
        allowed_options = {
            'qos': 'normal',
            'account': 'research',
            'constraint': 'skylake|cascadelake',
            'reservation': 'gpu-reservation',
            'exclude': 'node[001-003]',
            'nodelist': 'node[010-020]',
            'comment': 'SkyPilot managed',
            'mail-type': 'END',
            'mail-user': 'user@example.com',
        }
        result = _build_custom_sbatch_directives(allowed_options)
        for key, value in allowed_options.items():
            assert f'#SBATCH --{key}={value}' in result

    def test_time_no_longer_protected(self):
        """`time` is now user-overridable (issue #9370)."""
        assert 'time' not in _SBATCH_PROTECTED_OPTIONS
        result = _build_custom_sbatch_directives({'time': '2:00:00'})
        assert result == '\n#SBATCH --time=2:00:00'

    def test_time_short_form_allowed(self):
        result = _build_custom_sbatch_directives({'t': '5'})
        assert result == '\n#SBATCH --t=5'

    def test_time_validator_wired(self):
        """Smoke test that validate_sbatch_time runs when key is 'time'.

        Exhaustive accepted/rejected format coverage lives in
        TestValidateSbatchTime (test_slurm_utils.py); this test only
        verifies the validator is called from the build path.
        """
        # Accepted format passes through.
        result = _build_custom_sbatch_directives({'time': '4:00:00'})
        assert result == '\n#SBATCH --time=4:00:00'
        # Rejected format raises with our error message.
        with pytest.raises(ValueError, match='Invalid slurm.sbatch_options'):
            _build_custom_sbatch_directives({'time': 'garbage'})

    def test_time_short_form_invalid_raises(self):
        """Validator is also wired for the short form 't'."""
        with pytest.raises(ValueError, match='Invalid slurm.sbatch_options'):
            _build_custom_sbatch_directives({'t': 'bogus'})


class TestComputeTimeDirective:
    """Test _compute_time_directive() fallback dispatch."""

    @staticmethod
    def _partition(maxtime=None, default_time=None):
        return slurm_adaptor.SlurmPartition(
            name='cpu',
            is_default=True,
            maxtime=maxtime,
            default_time=default_time,
        )

    def test_user_supplied_time_skips_auto_directive(self):
        """User-supplied --time wins; auto-generated directive omitted."""
        partition = self._partition(maxtime=3600, default_time='01:00:00')
        result = _compute_time_directive({'time': '4:00:00'}, partition, 'cpu')
        assert result == ''

    def test_user_supplied_short_form_skips_auto_directive(self):
        partition = self._partition(maxtime=3600, default_time='01:00:00')
        result = _compute_time_directive({'t': '5'}, partition, 'cpu')
        assert result == ''

    def test_default_time_used_only_when_maxtime_unset(self):
        """Partition has DefaultTime but no MaxTime; omit --time so Slurm applies it.

        This is the #9370 fix: pre-existing code would emit
        --time=UNLIMITED here, which the backfill scheduler refuses to
        schedule ahead of maintenance reservations.
        """
        partition = self._partition(maxtime=None, default_time='01:00:00')
        result = _compute_time_directive({}, partition, 'cpu')
        assert result == ''

    def test_maxtime_wins_when_both_set(self):
        """When both MaxTime and DefaultTime are set, MaxTime wins.

        Preserves SkyPilot's longstanding behavior of always emitting
        --time=MaxTime. See `TODO(kevin)` in `_compute_time_directive`
        for arguments to reconsider this priority later.
        """
        partition = self._partition(maxtime=3600, default_time='00:15:00')
        result = _compute_time_directive({}, partition, 'cpu')
        # MaxTime (1h), not DefaultTime (15min).
        assert result == '#SBATCH --time=0-01:00:00'

    def test_no_maxtime_no_default_warns_and_emits_nothing(self, monkeypatch):
        """No MaxTime and no DefaultTime — warn and emit nothing."""
        partition = self._partition()
        # The `sky` logger has propagate=False, so caplog cannot observe
        # it. Mock the module logger's `warning` directly.
        warning_mock = mock.MagicMock()
        monkeypatch.setattr(slurm_instance.logger, 'warning', warning_mock)
        result = _compute_time_directive({}, partition, 'gpu')
        assert result == ''
        warning_mock.assert_called_once()
        msg = warning_mock.call_args.args[0]
        assert 'no MaxTime or DefaultTime' in msg
        assert 'gpu' in msg

    def test_explicit_null_time_treated_as_unset(self):
        """sbatch_options.time=null behaves the same as not setting it."""
        partition = self._partition(maxtime=3600)
        result = _compute_time_directive({'time': None}, partition, 'cpu')
        # MaxTime fallback kicks in because user did not actually supply time.
        assert result == '#SBATCH --time=0-01:00:00'


class TestBuildSbatchDirectives:
    """Test _build_sbatch_directives() combines auto --time + user options."""

    @staticmethod
    def _partition(maxtime=None, default_time=None):
        return slurm_adaptor.SlurmPartition(name='cpu',
                                            is_default=True,
                                            maxtime=maxtime,
                                            default_time=default_time)

    def test_only_auto_time(self):
        """Partition has MaxTime, user has no options."""
        result = _build_sbatch_directives({}, self._partition(maxtime=3600),
                                          'cpu')
        assert result == '\n#SBATCH --time=0-01:00:00'

    def test_only_user_options(self):
        """No auto --time, user supplies other options."""
        result = _build_sbatch_directives(
            {'qos': 'high'}, self._partition(default_time='01:00:00'), 'cpu')
        assert result == '\n#SBATCH --qos=high'

    def test_both_auto_time_and_user_options(self):
        """Auto --time and user options both present, no double newline."""
        result = _build_sbatch_directives({'qos': 'high'},
                                          self._partition(maxtime=3600), 'cpu')
        assert result == '\n#SBATCH --time=0-01:00:00\n#SBATCH --qos=high'

    def test_user_time_replaces_auto_time(self):
        """User-supplied time wins; only the user's --time appears."""
        result = _build_sbatch_directives({'time': '2:00:00'},
                                          self._partition(maxtime=3600), 'cpu')
        assert result == '\n#SBATCH --time=2:00:00'


class TestSbatchConfigSchema:
    """Test sbatch_options schema validation in config YAML."""

    def _validate(self, config):
        """Validate a config dict against the SkyPilot config schema."""
        schema = get_config_schema()
        jsonschema.validate(instance=config, schema=schema)

    def test_valid_cloud_level(self):
        self._validate({
            'slurm': {
                'sbatch_options': {
                    'qos': 'high',
                    'account': 'research',
                }
            }
        })

    def test_valid_cluster_level(self):
        self._validate({
            'slurm': {
                'cluster_configs': {
                    'mycluster': {
                        'sbatch_options': {
                            'qos': 'high',
                            'constraint': 'skylake',
                        }
                    }
                }
            }
        })

    def test_valid_partition_level(self):
        self._validate({
            'slurm': {
                'cluster_configs': {
                    'mycluster': {
                        'partition_configs': {
                            'gpu': {
                                'sbatch_options': {
                                    'qos': 'gpu-qos',
                                }
                            }
                        }
                    }
                }
            }
        })

    def test_valid_all_three_levels(self):
        self._validate({
            'slurm': {
                'sbatch_options': {
                    'account': 'default-account',
                },
                'cluster_configs': {
                    'mycluster': {
                        'sbatch_options': {
                            'qos': 'high',
                        },
                        'partition_configs': {
                            'gpu': {
                                'sbatch_options': {
                                    'constraint': 'a100',
                                }
                            }
                        },
                    }
                }
            }
        })

    def test_valid_value_types(self):
        self._validate({
            'slurm': {
                'sbatch_options': {
                    'qos': 'high',
                    'nice': 100,
                    'exclusive': True,
                    'requeue': False,
                    'comment': None,
                }
            }
        })

    def test_invalid_nested_object_value(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate({
                'slurm': {
                    'sbatch_options': {
                        'qos': {
                            'nested': 'not allowed'
                        },
                    }
                }
            })

    def test_invalid_list_value(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate(
                {'slurm': {
                    'sbatch_options': {
                        'qos': ['not', 'a', 'list'],
                    }
                }})

    def test_invalid_newline_in_value(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate({
                'slurm': {
                    'sbatch_options': {
                        'comment': 'innocent\ntouch /tmp/pwned',
                    }
                }
            })
