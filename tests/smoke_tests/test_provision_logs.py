"""Smoke tests for provisioning log streaming.

Validates that:
- sky logs --provision <cluster> streams the current request's provision.log
- After cluster removal, streaming falls back to the history entry
"""

import pytest
from smoke_tests import smoke_tests_utils

from sky import skypilot_config


@pytest.mark.no_vast
def test_provision_logs_streaming(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()

    # Tail last lines and do not follow so the command exits.
    tail_cmd = f'sky logs --provision {name} --no-follow --tail 10'

    def wait_stream_ok(cmd: str) -> str:
        # Retry up to ~60s for provision path to be recorded and file to exist.
        return ('attempts=12; ok=0; '
                'while [ $attempts -gt 0 ]; do '
                f'  out="$({cmd} 2>&1)"; rc=$?; echo "$out"; '
                '  if [ $rc -eq 0 ] && [ -n "$out" ] && '
                '     ! echo "$out" | grep -qE "HTTPError|Not Found|404"; then '
                '    ok=1; break; fi; '
                '  sleep 5; attempts=$((attempts-1)); '
                'done; [ $ok -eq 1 ]')

    commands = [
        # Launch detached so we can immediately query provision logs.
        (f'sky launch --infra {generic_cloud} -d -y '
         f'{smoke_tests_utils.LOW_RESOURCE_ARG} -c {name} examples/minimal.yaml'
        ),
        # Give the provisioner a moment to create and start writing provision.log
        'sleep 5',
        # Stream provision logs for an active cluster; ensure success and no HTTP errors.
        wait_stream_ok(tail_cmd),
        # Remove the cluster and ensure history-based lookup works.
        f'sky down -y {name}',
        # Fallback to history: ensure success and no HTTP errors.
        wait_stream_ok(tail_cmd),
    ]

    test = smoke_tests_utils.Test(
        'provision_logs_streaming',
        commands,
        teardown=f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud))
    smoke_tests_utils.run_one_test(test)
