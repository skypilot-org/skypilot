#!/bin/bash
set -e
echo "🔨 Building STIX..."
cargo build --workspace --release
echo "✅ Build complete"
