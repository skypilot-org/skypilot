#!/bin/bash

set -euo pipefail
set -x

# Default values if environment variables are not set
UNIQUE_ID=${BENCHMARK_UNIQUE_ID:-"test-$(date +%s)"}
CLOUD=${BENCHMARK_CLOUD:-"kubernetes"}

CLUSTER="load-test-${UNIQUE_ID}"

for i in {1..100}; do
    sky launch -y -c $CLUSTER --infra $CLOUD --cpus 2+
    sky down $CLUSTER -y
    sky show-gpus
done
sky cost-report --days 30 --all
