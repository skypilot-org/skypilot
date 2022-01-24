#!/bin/bash
# Run a few example apps as smoke tests.
# NOTE: after tests, shut down clusters manually in UI / sky down --all.
# Some tests are commented out due to long run time / being covered by others.
# Usage:
#   bash examples/run_smoke_tests.sh 2>&1 | tee run.log
set -ex

DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Dry run.  2 Tasks in a chain.
python "$DIR"/example_app.py

# Simple apps.
time sky launch -c min "$DIR"/minimal.yaml
sky down min &
time sky launch -c sc "$DIR"/source_check.yaml
sky down sc &
time sky launch -c fm "$DIR"/using_file_mounts.yaml
sky down fm &

# Task(), 1 node.
# 17:25.60 total
# time sky launch -c resnet "$DIR"/resnet_app.yaml
# 13.770 total
# time sky exec -c resnet "$DIR"/resnet_app.yaml
# time python "$DIR"/resnet_app.py
# sky down resnet &
# If concurrently running two runs of resnet, the second run will be
# waiting for the first run.

# Task(), 1 node.
# 6:47.90 total
time sky launch -c huggingface "$DIR"/huggingface_glue_imdb_app.yaml
# real    1m49.532s
time sky exec -c huggingface "$DIR"/huggingface_glue_imdb_app.yaml
# time python "$DIR"/huggingface_glue_imdb_app.py
sky down huggingface &

# Task(), 1 node, TPU.
# real    10m9.219s
time sky launch -c tpu "$DIR"/tpu_app.yaml
# real    3m26.997s
# time sky exec -c tpu "$DIR"/tpu_app.yaml
# python "$DIR"/tpu_app.py
sky down tpu &

# Task(), n nodes.
# real    4m17.406s
time sky launch -c mh "$DIR"/multi_hostname.yaml
# real    0m50.811s
time sky exec -c mh "$DIR"/multi_hostname.yaml
sky down mh &

# Task(), n nodes with setups.
time python "$DIR"/resnet_distributed_tf_app.py
time python "$DIR"/resnet_distributed_tf_app.py
sky down dtf &

# Submitting multiple tasks to the same cluster.
# 6:23.58 total
time python "$DIR"/multi_echo.py
# python "$DIR"/huggingface_glue_imdb_grid_search_app.py

# Job Queue.
time sky launch -c jq "$DIR"/job_queue/cluster.yaml
time sky exec -c jq -d "$DIR"/job_queue/job.yaml
time sky exec -c jq -d "$DIR"/job_queue/job.yaml
time sky exec -c jq -d "$DIR"/job_queue/job.yaml
sky logs -c jq 2
# TODO(suquark): wait all jobs to complete. use 'sleep' as a workaround now
sleep 30
sky queue jq
sky down jq &

time sky launch -c mjq "$DIR"/job_queue/cluster_multinode.yaml
time sky exec -c mjq -d "$DIR"/job_queue/job_multinode.yaml
time sky exec -c mjq -d "$DIR"/job_queue/job_multinode.yaml
time sky exec -c mjq -d "$DIR"/job_queue/job_multinode.yaml
# The job id is automatically incremented from 1 (inclusive).
sky cancel -c mjq 1
sky logs -c mjq 2
# TODO(suquark): wait all jobs to complete. use 'sleep' as a workaround now
sleep 30
sky queue mjq
sky down mjq &

wait
