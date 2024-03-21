#!/bin/bash

rm -rf build docs
script -q -c "make html" /tmp/build_docs.txt

# Check if the output contains "ERROR:" or "WARNING:"
if grep -q -E "ERROR:|WARNING:" /tmp/build_docs.txt; then
  echo "Errors or warnings detected, exiting..."
  exit 1
fi
