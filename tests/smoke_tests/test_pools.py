import tempfile
import textwrap

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky import skypilot_config
from sky.skylet import constants
from sky.skylet import events
from sky.utils import common_utils
from sky.utils import yaml_utils


@pytest.mark.gcp
def test_pools_setup_num_gpus():
    """Test that the number of GPUs is set correctly in the setup script."""

    setup_yaml = textwrap.dedent(f"""
    pool:
        workers: 1

    resources:
        accelerators: {{L4:2}}

    setup: |
        echo "SKYPILOT_NUM_GPUS_PER_NODE is $SKYPILOT_NUM_GPUS_PER_NODE"
        if [[ "$SKYPILOT_NUM_GPUS_PER_NODE" != "2" ]]; then
            exit 1
        fi
    """)

    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(setup_yaml.encode('utf-8'))
        f.flush()
        name = smoke_tests_utils.get_cluster_name()+'-lloyd'
        pool = f'{name}-pool'

        wait_until_pool_ready = (
            'start_time=$SECONDS; '
            'while true; do '
            'if (( $SECONDS - $start_time > {timeout} )); then '
            '  echo "Timeout after {timeout} seconds waiting for job to succeed"; exit 1; '
            'fi; '
            f's=$(sky jobs pool status {pool}); '
            'echo "$s"; '
            'if echo "$s" | grep "FAILED"; then '
            '  exit 1; '
            'fi; '
            'if echo "$s" | grep "SHUTTING_DOWN"; then '
            '  exit 1; '
            'fi; '
            'if echo "$s" | grep "READY"; then '
            '  break; '
            'fi; '
            'echo "Waiting for pool to be ready..."; '
            'sleep 5; '
            'done')


        test = smoke_tests_utils.Test(
            'test_pools_setup_num_gpus',
            [
                f's=$(sky jobs pool apply -p {pool} {f.name} -y); echo "$s"; echo; echo; echo "$s" | grep "Successfully created pool"',
                # Wait for the pool to be created.
                wait_until_pool_ready.format(timeout=smoke_tests_utils.get_timeout('gcp')),
            ],
            timeout=smoke_tests_utils.get_timeout('gcp'),
            teardown=f'sky jobs pool down {pool} -y && sleep 5 && controller=$(sky status -u | grep sky-jobs-controller- | awk \'NR==1{{print $1}}\') && echo "$controller" && echo delete | sky down "$controller" && exit 1 || true'
        )
        smoke_tests_utils.run_one_test(test)