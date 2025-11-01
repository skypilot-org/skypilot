#!/bin/bash
set -e
echo "🧪 Running tests..."
cargo test --workspace
echo "✅ Tests passed"
