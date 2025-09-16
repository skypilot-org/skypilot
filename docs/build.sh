#!/bin/bash

# Function to check if file exists and is less than 24 hours old
check_file_age() {
    if [ -f "$1" ] && [ $(( $(date +%s) - $(stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null) )) -lt 86400 ]; then
        return 0  # File exists and is recent
    fi
    return 1  # File doesn't exist or is old
}

# Only run sky show-gpus commands if output files don't exist or are old
if ! check_file_age "source/compute/show-gpus-all.txt"; then
    sky show-gpus -a > source/compute/show-gpus-all.txt
    sed '/^tpu-v2-128/,$d' source/compute/show-gpus-all.txt > source/compute/show-gpus-all.txt-new
    mv source/compute/show-gpus-all.txt-new source/compute/show-gpus-all.txt
    echo "... [omitted long outputs] ..." >> source/compute/show-gpus-all.txt
fi

if ! check_file_age "source/compute/show-gpus-h100-8.txt"; then
    sky show-gpus H100:8 > source/compute/show-gpus-h100-8.txt
fi

rm -rf build docs

# Add command line argument parsing
AUTO_BUILD=false
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --watch|-w) AUTO_BUILD=true ;;
        --port|-p) PORT=$2; shift ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

if [ "$AUTO_BUILD" = true ]; then
    # Use sphinx-autobuild for automatic rebuilding
    # Ignore gallery directory to prevent unnecessary rebuilds
    sphinx-autobuild source build/html \
        --ignore "*.md" \
        --port $PORT
else
    rm -rf build docs

    # MacOS and GNU `script` have different usages
    if [ "$(uname -s)" = "Linux" ]; then
        script -q /tmp/build_docs.txt -c "make html"
    else
        # Assume MacOS (uname -s = Darwin)
        script -q /tmp/build_docs.txt bash -c "make html"
    fi

    # Check if the output contains "ERROR:" or "WARNING:"
    if grep -q -E "ERROR:|WARNING:|CRITICAL:" /tmp/build_docs.txt; then
        echo "Errors or warnings detected, exiting..."
        exit 1
    fi
fi
