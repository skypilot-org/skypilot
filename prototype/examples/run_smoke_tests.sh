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
time sky run -c min "$DIR"/minimal.yaml
sky down min &
time sky run -c fm "$DIR"/using_file_mounts.yaml
sky down fm &

# Task(), 1 node.
# 17:25.60 total
# time sky run -c resnet "$DIR"/resnet_app.yaml
# 13.770 total
# time sky exec -c resnet "$DIR"/resnet_app.yaml
# time python "$DIR"/resnet_app.py
# sky down resnet &
# FIXME: if concurrently running two runs of resnet, the second run should be
# waiting for the first run, but this is not happening.

# Task(), 1 node.
# 6:47.90 total
time sky run -c huggingface "$DIR"/huggingface_glue_imdb_app.yaml
# real    1m49.532s
time sky exec -c huggingface "$DIR"/huggingface_glue_imdb_app.yaml
# time python "$DIR"/huggingface_glue_imdb_app.py
sky down huggingface &

# Task(), 1 node, TPU.
# real    10m9.219s
time sky run -c tpu "$DIR"/tpu_app.yaml
# real    3m26.997s
# time sky exec -c tpu "$DIR"/tpu_app.yaml
# python "$DIR"/tpu_app.py
sky down tpu &

# Task(), n nodes.
# real    4m17.406s
time sky run -c mh "$DIR"/multi_hostname.yaml
# real    0m50.811s
time sky exec -c mh "$DIR"/multi_hostname.yaml
sky down mh &

# Task(), n nodes with setups.
# real    4m17.406s
time python "$DIR"/resnet_distributed_tf_app.py
# real    0m50.811s
time python "$DIR"/multi_hostname.yaml
sky down dtf &

# ParTask.
# 6:23.58 total
time python "$DIR"/multi_echo.py
# python "$DIR"/huggingface_glue_imdb_grid_search_app.py

wait
