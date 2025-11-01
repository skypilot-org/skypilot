# ?? Styx - Production Dockerfile

# Build stage
FROM rust:1.82-slim as builder

WORKDIR /build

# Install dependencies
RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy workspace
COPY Cargo.toml Cargo.lock ./
COPY crates ./crates

# Build release binaries
RUN cargo build --release --bins

# Runtime stage
FROM debian:bookworm-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    ca-certificates \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Copy binaries
COPY --from=builder /build/target/release/styx /usr/local/bin/
COPY --from=builder /build/target/release/styx-server /usr/local/bin/
COPY --from=builder /build/target/release/styx-agent /usr/local/bin/

# Expose server port
EXPOSE 8080

# Default to running server
CMD ["styx-server"]
