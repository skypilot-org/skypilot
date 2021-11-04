#!/bin/bash
# Run a few example apps as smoke tests.
set -ex

DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

python $DIR/example_app.py
python $DIR/resnet_app.py
python $DIR/huggingface_glue_imdb_app.py
python $DIR/multi_echo.py
