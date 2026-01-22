#!/bin/bash

# Configuration
START_IDX=0
# END_IDX=29475453  # Last index of the review dataset
END_IDX=600000  # Last index of the review dataset
NUM_JOBS=10
POOL_NAME="vllm-pool"  # Pool name for the jobs

echo "Launching $NUM_JOBS jobs to process range [$START_IDX, $END_IDX)"
echo "Using pool: $POOL_NAME"
echo ""

JOB_NAME="vector-compute-worker"

echo "sky jobs launch -y --name $JOB_NAME --pool $POOL_NAME --num-jobs $NUM_JOBS \\
  --env NUM_WORKERS=$NUM_JOBS \\
  --env GLOBAL_START_IDX=$START_IDX \\
  --env GLOBAL_END_IDX=$END_IDX \\
  compute_text_vectors.yaml"
