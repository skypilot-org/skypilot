#!/bin/bash
set -e
echo "🔍 Linting code..."
cargo clippy --workspace -- -D warnings
echo "✅ Lint passed"
