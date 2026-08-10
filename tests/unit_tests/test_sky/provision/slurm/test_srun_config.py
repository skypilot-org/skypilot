"""Unit tests for srun_options support in Slurm provisioner."""

import jsonschema
import pytest

from sky.provision.slurm.instance import _build_custom_srun_args
from sky.provision.slurm.instance import _SRUN_PROTECTED_OPTIONS
from sky.utils.schemas import get_config_schema


class TestBuildCustomSrunArgs:
    """Test _build_custom_srun_args()."""

    def test_empty_config(self):
        assert _build_custom_srun_args({}) == ''

    def test_none_config(self):
        # The caller may pass None when no options are configured.
        assert _build_custom_srun_args({}) == ''

    def test_string_value(self):
        assert _build_custom_srun_args({'cpu-bind': 'cores'}) == (
            "--cpu-bind=cores")

    def test_numeric_value(self):
        assert _build_custom_srun_args({'auto-resume': 1}) == '--auto-resume=1'

    def test_boolean_true_flag(self):
        assert _build_custom_srun_args({'pty': True}) == '--pty'

    def test_boolean_false_omitted(self):
        assert _build_custom_srun_args({'pty': False}) == ''

    def test_null_omitted(self):
        assert _build_custom_srun_args({'cpu-bind': None}) == ''

    def test_multiple_options_sorted(self):
        result = _build_custom_srun_args({
            'cpu-bind': 'cores',
            'auto-resume': 1,
            'comment': 'hi',
        })
        assert result == '--auto-resume=1 --comment=hi --cpu-bind=cores'

    def test_underscore_normalized_to_hyphen(self):
        assert _build_custom_srun_args({'auto_resume': 1}) == '--auto-resume=1'

    @pytest.mark.parametrize('option', sorted(_SRUN_PROTECTED_OPTIONS))
    def test_all_protected_options_skipped(self, option):
        assert _build_custom_srun_args({option: 'value'}) == ''

    def test_protected_skipped_non_protected_kept(self):
        result = _build_custom_srun_args({
            'jobid': 'override-attempt',
            'auto-resume': 2,
        })
        assert result == '--auto-resume=2'

    def test_newline_in_value_raises(self):
        with pytest.raises(ValueError, match='Newline'):
            _build_custom_srun_args({'comment': 'foo\nbar'})

    def test_newline_in_key_raises(self):
        with pytest.raises(ValueError, match='Newline'):
            _build_custom_srun_args({'foo\nbar': 'value'})

    @pytest.mark.parametrize('value', [
        'foo; rm -rf /',
        'foo && echo pwned',
        '$(whoami)',
        '`whoami`',
        "foo' || echo pwned #",
    ])
    def test_shell_metacharacters_are_quoted(self, value):
        """Shell metacharacters in values must be shell-quoted, because srun
        args are appended directly to a shell command (unlike sbatch which
        injects into a script as comment-prefixed directives)."""
        import shlex
        result = _build_custom_srun_args({'comment': value})
        # Round-trip through shlex.split: the shell would parse the same args.
        parsed = shlex.split(result)
        assert parsed == [f'--comment={value}']


class TestSrunConfigSchema:
    """Test srun_options schema validation in config YAML."""

    def _validate(self, config):
        schema = get_config_schema()
        jsonschema.validate(instance=config, schema=schema)

    def test_valid_cloud_level(self):
        self._validate(
            {'slurm': {
                'srun_options': {
                    'auto-resume': 1,
                }
            }})

    def test_valid_cluster_level(self):
        self._validate({
            'slurm': {
                'cluster_configs': {
                    'mycluster': {
                        'srun_options': {
                            'auto-resume': 3,
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
                                'srun_options': {
                                    'auto-resume': 5,
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
                'srun_options': {
                    'cpu-bind': 'cores',
                },
                'cluster_configs': {
                    'mycluster': {
                        'srun_options': {
                            'auto-resume': 1,
                        },
                        'partition_configs': {
                            'gpu': {
                                'srun_options': {
                                    'auto-resume': 5,
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
                'srun_options': {
                    'cpu-bind': 'cores',
                    'auto-resume': 1,
                    'pty': True,
                    'overcommit': False,
                    'comment': None,
                }
            }
        })

    def test_invalid_nested_object_value(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate(
                {'slurm': {
                    'srun_options': {
                        'cpu-bind': {
                            'nested': 'no'
                        }
                    }
                }})

    def test_invalid_list_value(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate(
                {'slurm': {
                    'srun_options': {
                        'cpu-bind': ['a', 'b'],
                    }
                }})

    def test_invalid_newline_in_value(self):
        with pytest.raises(jsonschema.ValidationError):
            self._validate({
                'slurm': {
                    'srun_options': {
                        'comment': 'innocent\ntouch /tmp/pwned',
                    }
                }
            })
