#!/bin/bash

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
    if grep -q -E "ERROR:|WARNING:" /tmp/build_docs.txt; then
        echo "Errors or warnings detected, exiting..."
        exit 1
    fi
fi
