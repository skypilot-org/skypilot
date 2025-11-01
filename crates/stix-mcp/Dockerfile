FROM rust:1.75 as builder

WORKDIR /app

# Copy Cargo files
COPY Cargo.toml ./
COPY crates/stix-mcp/Cargo.toml ./crates/stix-mcp/

# Copy source
COPY crates/stix-mcp/src ./crates/stix-mcp/src
COPY crates/stix-core ./crates/stix-core

# Build the application
RUN cargo build --release --package stix-mcp

# Runtime stage
FROM debian:bookworm-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    ca-certificates \
    grep \
    && rm -rf /var/lib/apt/lists/*

# Create app user
RUN useradd -m -u 1000 mcpuser

WORKDIR /app

# Copy binary from builder
COPY --from=builder /app/target/release/stix-mcp /app/stix-mcp

# Change ownership
RUN chown -R mcpuser:mcpuser /app

USER mcpuser

# Expose port
EXPOSE 8080

# Environment variables
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8080
ENV MCP_WORKSPACE_ROOT=/workspace
ENV MCP_LOG_FILE=/app/logs/mcp-audit.log
ENV MCP_RATE_LIMIT=100

# Run the application
CMD ["./stix-mcp"]
