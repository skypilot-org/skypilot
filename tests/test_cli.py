import tempfile
import textwrap

from click import testing as cli_testing

import sky
from sky import clouds
import sky.cli as cli


def test_infer_gpunode_type():
    resources = [
        sky.Resources(cloud=sky.AWS(), instance_type='p3.2xlarge'),
        sky.Resources(cloud=sky.GCP(), accelerators='K80'),
        sky.Resources(accelerators={'V100': 8}),
        sky.Resources(cloud=sky.Azure(), accelerators='A100'),
    ]
    for spec in resources:
        assert cli._infer_interactive_node_type(spec) == 'gpunode', spec


def test_infer_cpunode_type():
    resources = [
        sky.Resources(cloud=sky.AWS(), instance_type='m5.2xlarge'),
        sky.Resources(cloud=sky.GCP()),
        sky.Resources(),
    ]
    for spec in resources:
        assert cli._infer_interactive_node_type(spec) == 'cpunode', spec


def test_infer_tpunode_type():
    resources = [
        sky.Resources(cloud=sky.GCP(), accelerators='tpu-v3-8'),
        sky.Resources(cloud=sky.GCP(), accelerators='tpu-v2-32'),
        sky.Resources(cloud=sky.GCP(),
                      accelerators={'tpu-v2-128': 1},
                      accelerator_args={'tpu_name': 'tpu'}),
    ]
    for spec in resources:
        assert cli._infer_interactive_node_type(spec) == 'tpunode', spec


def test_accelerator_mismatch(monkeypatch):
    """Test the specified accelerator does not match the instance_type."""
    # Monkey-patching is required because in the test environment, no cloud is
    # enabled. The optimizer checks the environment to find enabled clouds, and
    # only generates plans within these clouds. The tests assume that all three
    # clouds are enabled, so we monkeypatch the `sky.global_user_state` module
    # to return all three clouds. We also monkeypatch `sky.check.check` so that
    # when the optimizer tries calling it to update enabled_clouds, it does not
    # raise exceptions.
    enabled_clouds = list(clouds.CLOUD_REGISTRY.values())
    monkeypatch.setattr(
        'sky.global_user_state.get_enabled_clouds',
        lambda: enabled_clouds,
    )
    monkeypatch.setattr('sky.check.check', lambda *_args, **_kwargs: None)
    config_file_backup = tempfile.NamedTemporaryFile(
        prefix='tmp_backup_config_default', delete=False)
    monkeypatch.setattr('sky.clouds.gcp.GCP_CONFIG_SKY_BACKUP_PATH',
                        config_file_backup.name)

    spec = textwrap.dedent("""\
        resources:
          cloud: aws
          instance_type: p3.2xlarge""")
    cli_runner = cli_testing.CliRunner()

    def _capture_mismatch_gpus_spec(file_path, gpus: str):
        result = cli_runner.invoke(cli.launch,
                                   [file_path, '--gpus', gpus, '--dryrun'])
        assert isinstance(result.exception, ValueError)
        assert 'Infeasible resource demands found:' in str(result.exception)

    def _capture_match_gpus_spec(file_path, gpus: str):
        result = cli_runner.invoke(cli.launch,
                                   [file_path, '--gpus', gpus, '--dryrun'])
        assert not result.exit_code

    with tempfile.NamedTemporaryFile('w', suffix='.yml') as f:
        f.write(spec)
        f.flush()

        _capture_mismatch_gpus_spec(f.name, 'T4:1')
        _capture_mismatch_gpus_spec(f.name, 'T4:0.5')
        _capture_mismatch_gpus_spec(f.name, 'V100:2')
        _capture_mismatch_gpus_spec(f.name, 'v100:2')

        _capture_match_gpus_spec(f.name, 'V100:1')
        _capture_match_gpus_spec(f.name, 'v100:1')
        _capture_match_gpus_spec(f.name, 'V100:0.5')
        _capture_match_gpus_spec(f.name, 'V100')
