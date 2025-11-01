#!/bin/bash
# Build manylinux wheels for SkyPilot Rust utilities
#
# Usage: ./build_wheels.sh [--release]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/skypilot-utils"

RELEASE_FLAG=""
if [[ "$1" == "--release" ]]; then
    RELEASE_FLAG="--release"
    echo "Building release wheels..."
else
    echo "Building debug wheels..."
fi

# Check if maturin is installed
if ! command -v maturin &> /dev/null; then
    echo "Error: maturin is not installed. Install with: pip install maturin"
    exit 1
fi

# Build for current platform
echo "Building wheel for current platform..."
maturin build $RELEASE_FLAG

# Optional: Build for manylinux using Docker
if command -v docker &> /dev/null; then
    read -p "Build manylinux wheels with Docker? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Building manylinux wheels..."
        docker run --rm -v "$(pwd)":/io \
            ghcr.io/pyo3/maturin build $RELEASE_FLAG --manylinux 2_28
    fi
fi

echo "Wheels built successfully!"
echo "Output directory: $(pwd)/target/wheels/"
ls -lh target/wheels/
