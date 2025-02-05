#!/bin/bash

# Run `sky show-gpus` and save the output.
sky show-gpus > source/compute/show-gpus.txt
sky show-gpus -a > source/compute/show-gpus-all.txt
sed -i '' '/^tpu-v2-128/,$d' source/compute/show-gpus-all.txt && echo "... [omitted long outputs] ..." >> source/compute/show-gpus-all.txt
sky show-gpus H100:8 > source/compute/show-gpus-h100-8.txt

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
