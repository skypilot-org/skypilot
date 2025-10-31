# 🦀 SkyPilot-R

**SkyPilot-R** (codename: **Styx**) - Complete Rust rewrite of SkyPilot for cloud orchestration.

## 🎯 Vision

High-performance, memory-safe, cloud-agnostic orchestration platform built entirely in Rust.

## 🚀 Status

**Phase 1: Core + CLI** - ✅ **ACTIVE DEVELOPMENT**

- ✅ Core scheduler with DAG support
- ✅ Task management & execution
- ✅ Resource allocation
- ✅ CLI tool (`sky`)
- 🚧 Cloud providers (next)
- 🚧 gRPC server (next)

## 📦 Architecture

```
skypilot-r/
├── crates/
│   ├── core/       ✅ Engine, Scheduler, Task Graph
│   ├── cli/        ✅ Command-line tool
│   ├── cloud/      🚧 AWS/GCP/Azure/K8s abstractions
│   ├── server/     🚧 REST/gRPC API
│   ├── db/         🚧 Persistence layer
│   ├── agent/      🚧 Node controller
│   ├── ui/         🚧 Web dashboard
│   └── sdk/        🚧 Rust SDK
└── tools/
    ├── benchmark/
    └── migration/
```

## ⚡ Quick Start

### Install

```bash
# Build from source
cd skypilot-r
cargo build --release

# Binary will be at: target/release/sky
```

### Usage

```bash
# Run a task
sky run --name "hello" echo "Hello from Rust!"

# Check status
sky status

# View stats
sky stats

# Show version
sky version
```

## 🏗️ Development

### Build

```bash
# Build all crates
cargo build --release

# Build specific crate
cargo build -p skypilot-core

# Run tests
cargo test

# Run CLI
cargo run -p skypilot-cli -- --help
```

### Code Quality

```bash
# Format
cargo fmt --all

# Lint
cargo clippy --all-targets --all-features

# Check
cargo check --all
```

## 📊 Performance

Target benchmarks (vs Python SkyPilot):

- **Task submission**: 10-20x faster
- **Scheduler throughput**: 15-30x faster
- **Memory usage**: 60-80% reduction
- **Binary size**: <20 MB
- **Startup time**: <50ms

## 🛠️ Technology Stack

- **Core**: Tokio, Petgraph
- **CLI**: Clap, Colored
- **API**: Axum, Tonic (planned)
- **DB**: SQLx, SeaORM (planned)
- **Cloud**: AWS SDK, GCP SDK, Kube (planned)

## 📖 Documentation

- **Getting Started**: [docs/getting-started.md](docs/getting-started.md) (planned)
- **Architecture**: [docs/architecture.md](docs/architecture.md) (planned)
- **API Reference**: Run `cargo doc --open`

## 🎯 Roadmap

### Phase 1: Core + CLI (✅ Current - 2 months)
- ✅ Core scheduler, tasks, resources
- ✅ Basic CLI
- ✅ In-memory execution

### Phase 2: Cloud + DB (3 months)
- AWS/GCP/Azure providers
- Persistent state (PostgreSQL)
- Credential management

### Phase 3: Server + Agent (2 months)
- REST/gRPC API server
- Remote node agents
- SSH orchestration

### Phase 4: UI + SDK (2 months)
- Web dashboard
- Rust SDK
- Client libraries

### Phase 5: Production (2 months)
- Testing & benchmarks
- CI/CD
- Documentation

**Total**: ~11 months to v1.0.0

## 🤝 Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) (planned).

## 📄 License

Apache-2.0

## 🙏 Acknowledgments

Based on the original [SkyPilot](https://github.com/skypilot-org/skypilot) project.

---

**Status**: 🚧 Alpha - Active Development  
**Version**: 0.1.0-alpha  
**Rust**: 1.75+
