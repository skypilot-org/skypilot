#!/bin/bash

declare -a services=("protos")

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
WORKDIR=$(dirname $SCRIPT_DIR)

for SERVICE in "${services[@]}"; do
    DESTDIR="$WORKDIR/generated"
    mkdir -p $DESTDIR
    python -m grpc_tools.protoc \
        --proto_path="$WORKDIR/$SERVICE/" \
        --python_out=$DESTDIR \
        --grpc_python_out=$DESTDIR \
        "$WORKDIR/$SERVICE/"*.proto
done
