"""Unit tests for sbatch_options support in Slurm provisioner."""

import subprocess

import jsonschema
import pytest

from sky.provision.slurm.instance import _build_custom_sbatch_directives
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
