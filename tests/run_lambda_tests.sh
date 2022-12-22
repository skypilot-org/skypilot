#!/bin/bash
# Usage:
#
#   # Run everything
#   bash tests/run_lambda_tests.sh
#
#   # Re-run a failed test
#   bash tests/run_lambda_tests.sh test_azure_start_stop
#

test=${1:-""}
if [ -z "$test" ]
then
    test_spec=tests/test_lambda.py
else
    test_spec=tests/test_lambda.py::"${test}"
fi

# We run tests sequentially (-n 1) to avoid Lambda Labs' API rate limits.
pytest -s -n 1 -q --tb=short --disable-warnings "$test_spec"

# To run all tests including the slow ones, add the --runslow flag:
# pytest --runslow -s -n 1 -q --tb=short --disable-warnings tests/test_lambda.py
