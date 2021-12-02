#!/bin/bash
# Run a few example apps as smoke tests.
# For now, must manually (i) Ctrl-C as training starts (ii) comment out this
# test and rerun this script for the remaining tests.
set -ex

DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

python "$DIR"/example_app.py

# FIXME: if concurrently running the below two runs, the second run should be
# waiting for the first run, but this is not happening.
python "$DIR"/resnet_app.py
sky run "$DIR"/resnet_app.yaml

python "$DIR"/huggingface_glue_imdb_app.py
python "$DIR"/multi_echo.py
python "$DIR"/huggingface_glue_imdb_grid_search_app.py
python "$DIR"/tpu_app.py

sky run "$DIR"/multi_hostname.yaml
