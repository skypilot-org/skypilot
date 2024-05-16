#!/bin/bash

rm -rf build docs
script -q /tmp/build_docs.txt bash -c "make html"

# Check if the output contains "ERROR:" or "WARNING:"
if grep -q -E "ERROR:|WARNING:" /tmp/build_docs.txt; then
  echo "Errors or warnings detected, exiting..."
  exit 1
fi
