#!/bin/bash

# Configuration
START_IDX=0
# END_IDX=29475453  # Last index of the review dataset
END_IDX=600000  # Last index of the review dataset
CHUNK_SIZE=60000
POOL_NAME="vllm-pool"  # Pool name for the jobs

# Calculate total number of jobs
TOTAL_RANGE=$((END_IDX - START_IDX))
NUM_JOBS=$(((TOTAL_RANGE + CHUNK_SIZE - 1) / CHUNK_SIZE))  # Ceiling division

echo "Launching $NUM_JOBS jobs to process range [$START_IDX, $END_IDX) with chunk size $CHUNK_SIZE"
echo "Using pool: $POOL_NAME"
echo ""

# Launch jobs
for ((JOB_RANK=0; JOB_RANK<NUM_JOBS; JOB_RANK++)); do
    
    JOB_NAME="vector-compute-worker-${JOB_RANK}"
    
    echo "Launching $JOB_NAME..."
    
    sky jobs launch --async -y --name $JOB_NAME --pool $POOL_NAME \
        --env WORKER_RANK=$JOB_RANK \
        --env TOTAL_WORKERS=$NUM_JOBS \
        --env GLOBAL_START_IDX=$START_IDX \
        --env GLOBAL_END_IDX=$END_IDX \
        compute_text_vectors.yaml
done
