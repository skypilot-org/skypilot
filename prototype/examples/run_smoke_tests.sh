#!/bin/bash
# Run a few example apps as smoke tests.
# For now, must manually (i) Ctrl-C as training starts (ii) comment out this
# test and rerun this script for the remaining tests.
#
# Usage:
#
#   bash examples/run_smoke_tests.sh 2>&1 | tee run.log
#
set -ex

DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Dry run.  2 Tasks in a chain.
python "$DIR"/example_app.py

# Simple apps.
time sky run -c min "$DIR"/minimal.yaml
sky down min
time sky run -c fm "$DIR"/using_file_mounts.yaml
sky down mh

# Task(), 1 node.
# time sky run -c resnet "$DIR"/resnet_app.yaml
# time sky exec -c resnet "$DIR"/resnet_app.yaml
# time python "$DIR"/resnet_app.py
# sky down resnet
# FIXME: if concurrently running two runs of resnet, the second run should be
# waiting for the first run, but this is not happening.

# Task(), 1 node.
time sky run -c huggingface "$DIR"/huggingface_glue_imdb_app.yaml
time sky exec -c huggingface "$DIR"/huggingface_glue_imdb_app.yaml
# time python "$DIR"/huggingface_glue_imdb_app.py
sky down huggingface

# Task(), 1 node, TPU.
time sky run -c tpu "$DIR"/tpu_app.yaml
time sky exec -c tpu "$DIR"/tpu_app.yaml
# python "$DIR"/tpu_app.py
sky down tpu

# Task(), n nodes.
time sky run -c mh "$DIR"/multi_hostname.yaml
time sky exec -c mh "$DIR"/multi_hostname.yaml
sky down mh

# ParTask.
time python "$DIR"/multi_echo.py
# python "$DIR"/huggingface_glue_imdb_grid_search_app.py
