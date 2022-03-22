#!/bin/bash
# Usage:
#
#   # Run everything
#   bash tests/run_smoke_tests.sh
#
#   # Re-run a failed test
#   bash tests/run_smoke_tests.sh test_azure_start_stop
#

test=${1:-""}
if [ -z "$test" ]
then
    test_spec=tests/test_smoke.py
else
    test_spec=tests/test_smoke.py::"$test"
fi

pytest -s -n 16 -q --tb=short --disable-warnings "$test_spec"

# To run all tests including the slow ones, add the --runslow flag:
# pytest --runslow -s -n 16 -q --tb=short --disable-warnings tests/test_smoke.py
