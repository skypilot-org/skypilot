#!/bin/bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
WORKDIR=$(dirname $SCRIPT_DIR)
rm $WORKDIR/generated/*pb2*.py
