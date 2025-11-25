#!/bin/bash

set -euo pipefail
set -x

# Default values if environment variables are not set
UNIQUE_ID=${BENCHMARK_UNIQUE_ID:-"test-$(date +%s)"}
CLOUD=${BENCHMARK_CLOUD:-"kubernetes"}
THREAD_ID=${BENCHMARK_THREAD_ID:-"0"}
REPEAT_ID=${BENCHMARK_REPEAT_ID:-"0"}

CLUSTER="load-test-${UNIQUE_ID}"
JOB="job-${UNIQUE_ID}"

sky check || true
sky show-gpus --infra $CLOUD || true
workdir=$(mktemp -d)
dd if=/dev/zero of=${workdir}/file.txt bs=1024 count=10000
sky jobs launch -y -n $JOB --infra $CLOUD 'for i in {1..120}; do echo "$i" && sleep 0.1; done' --workdir ${workdir}
sky jobs queue
sky jobs logs -n $JOB || true
sky jobs logs -n $JOB --controller
