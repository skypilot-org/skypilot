#!/bin/bash

rm -rf build docs

# MacOS and GNU `script` have different usages
if [ "$(uname -s)" = "Linux" ]; then
  script -q /tmp/build_docs.txt -c "make html"
else
  # Assume MacOS (uname -s = Darwin)
  script -q /tmp/build_docs.txt bash -c "make html"
fi

# Check if the output contains "ERROR:" or "WARNING:"
if grep -q -E "ERROR:|WARNING:" /tmp/build_docs.txt; then
  echo "Errors or warnings detected, exiting..."
  exit 1
fi
