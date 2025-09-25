import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils


@pytest.mark.runpod
def test_runpod_volume_ls():
    name = smoke_tests_utils.get_cluster_name()
    volume_name = f'rpv-{name}'
    volume_config = textwrap.dedent(f"""
        name: {volume_name}
        type: runpod-network-volume
        infra: runpod
        size: 1Gi
    """)
    with tempfile.NamedTemporaryFile(suffix='.yaml', mode='w') as f:
        f.write(volume_config)
        f.flush()
        volume_config_path = f.name
        test = smoke_tests_utils.Test(
            'test_runpod_volume_ls',
            [
                f's=$(sky volumes apply {volume_config_path} -y); echo "$s"; echo; echo; echo "$s" | grep "Creating"',
                f'sky volumes ls | grep {volume_name}',
            ],
            f's=$(sky volumes delete {volume_name} -y); echo "$s"; echo; echo; echo "$s" | grep "Deleting"',
        )
        smoke_tests_utils.run_one_test(test)