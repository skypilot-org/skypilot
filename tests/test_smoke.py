# Smoke tests for SkyPilot
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/test_smoke.py
#
# Terminate failed clusters after test finishes
# > pytest tests/test_smoke.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/test_smoke.py::test_minimal
#
# Only run managed job tests
# > pytest tests/test_smoke.py --managed-jobs
#
# Only run sky serve tests
# > pytest tests/test_smoke.py --sky-serve
#
# Only run test for AWS + generic tests
# > pytest tests/test_smoke.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/test_smoke.py --generic-cloud aws

from smoke_tests.test_api_server import *
from smoke_tests.test_aws_logs import *
from smoke_tests.test_basic import *
from smoke_tests.test_cli import *
from smoke_tests.test_cluster_job import *
from smoke_tests.test_examples import *
from smoke_tests.test_images import *
from smoke_tests.test_logs import *
from smoke_tests.test_managed_job import *
from smoke_tests.test_mount_and_storage import *
from smoke_tests.test_pools import *
from smoke_tests.test_region_and_zone import *
from smoke_tests.test_sky_serve import *
from smoke_tests.test_ssm import *
from smoke_tests.test_workspaces import *
