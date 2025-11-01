#!/bin/bash
set -e

echo "🚀 Starting STIX MCP Server with Admin UI"
echo ""

# Check if we're in the right directory
if [ ! -f "Cargo.toml" ]; then
    echo "❌ Error: Must run from crates/stix-mcp directory"
    exit 1
fi

# Build UI if needed
if [ ! -d "admin-ui/dist" ] || [ "$1" == "--rebuild-ui" ]; then
    echo "📦 Building Admin UI..."
    cd admin-ui
    
    if [ ! -d "node_modules" ]; then
        echo "📥 Installing dependencies..."
        npm install
    fi
    
    npm run build
    cd ..
    echo "✅ UI built successfully"
    echo ""
fi

# Set default environment variables
export MCP_HOST="${MCP_HOST:-0.0.0.0}"
export MCP_PORT="${MCP_PORT:-8080}"
export MCP_WORKSPACE_ROOT="${MCP_WORKSPACE_ROOT:-.}"
export MCP_LOG_FILE="${MCP_LOG_FILE:-mcp-audit.log}"
export MCP_RATE_LIMIT="${MCP_RATE_LIMIT:-100}"
export MCP_ADMIN_PASSWORD="${MCP_ADMIN_PASSWORD:-admin123}"
export RUST_LOG="${RUST_LOG:-stix_mcp=info}"

echo "⚙️  Configuration:"
echo "   Host: $MCP_HOST"
echo "   Port: $MCP_PORT"
echo "   Workspace: $MCP_WORKSPACE_ROOT"
echo "   Log File: $MCP_LOG_FILE"
echo "   Rate Limit: $MCP_RATE_LIMIT requests/min"
echo ""

if [ "$MCP_ADMIN_PASSWORD" == "admin123" ]; then
    echo "⚠️  WARNING: Using default admin password!"
    echo "   Set MCP_ADMIN_PASSWORD environment variable in production"
    echo ""
fi

echo "🔧 Building Rust server..."
cargo build --release

echo ""
echo "🎉 Starting server..."
echo ""
echo "📍 Server URL: http://${MCP_HOST}:${MCP_PORT}"
echo "🎨 Admin UI: http://localhost:${MCP_PORT}/admin"
echo "📖 API Docs: http://localhost:${MCP_PORT}/health"
echo ""
echo "Press Ctrl+C to stop"
echo ""

cargo run --release
