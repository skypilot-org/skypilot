#!/bin/bash
# Run a few example apps as smoke tests.
# For now, must manually (i) Ctrl-C as training starts (ii) comment out this
# test and rerun this script for the remaining tests.
set -ex

DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

python $DIR/example_app.py
python $DIR/resnet_app.py
python $DIR/huggingface_glue_imdb_app.py
python $DIR/multi_echo.py
