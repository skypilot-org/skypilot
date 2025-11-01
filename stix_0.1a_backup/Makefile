# ?? Styx Makefile

.PHONY: help build test clean run docker

help:
	@echo "?? Styx - Available commands:"
	@echo ""
	@echo "  make build          - Build all binaries (release)"
	@echo "  make test           - Run all tests"
	@echo "  make clean          - Clean build artifacts"
	@echo "  make run-cli        - Run CLI"
	@echo "  make run-server     - Run server"
	@echo "  make run-agent      - Run agent"
	@echo "  make docker         - Build Docker image"
	@echo "  make docker-up      - Start with docker-compose"
	@echo "  make docker-down    - Stop docker-compose"
	@echo "  make fmt            - Format code"
	@echo "  make clippy         - Run clippy"
	@echo "  make check          - Check compilation"

build:
	@echo "?? Building Styx..."
	cargo build --release

test:
	@echo "?? Running tests..."
	cargo test --all

clean:
	@echo "?? Cleaning..."
	cargo clean

run-cli:
	@echo "?? Running CLI..."
	cargo run --release -p styx-cli -- version

run-server:
	@echo "?? Starting server..."
	cargo run --release -p styx-server

run-agent:
	@echo "?? Starting agent..."
	cargo run --release -p styx-agent

docker:
	@echo "?? Building Docker image..."
	docker build -t styx:latest .

docker-up:
	@echo "?? Starting docker-compose..."
	docker-compose up -d

docker-down:
	@echo "?? Stopping docker-compose..."
	docker-compose down

fmt:
	@echo "?? Formatting code..."
	cargo fmt --all

clippy:
	@echo "?? Running clippy..."
	cargo clippy --all -- -D warnings

check:
	@echo "? Checking compilation..."
	cargo check --all
