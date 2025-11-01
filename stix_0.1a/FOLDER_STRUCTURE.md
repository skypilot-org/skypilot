# STIX - Improved Folder Structure Design

> Rust-based reorganization of SkyPilot with better modularity and Rust best practices

**Date**: November 1, 2025  
**Target**: `/home/wind/ide/skypilot/stix_0.1a`

---

## 🎯 Design Principles

1. **Modular Crate Structure**: Clear separation of concerns across crates
2. **Rust Best Practices**: Follow Cargo workspace conventions
3. **Python Compatibility**: Mirror SkyPilot's logical structure for easier migration
4. **Scalability**: Easy to add new clouds, backends, features
5. **Testability**: Each crate is independently testable
6. **Documentation**: Clear module boundaries and responsibilities

---

## 📁 New Folder Structure

```
stix/
├── Cargo.toml                    # Workspace root
├── Cargo.lock
├── README.md
├── TODO.md                       # Feature tracking
├── LICENSE
├── .gitignore
│
├── docs/                         # Documentation
│   ├── architecture.md
│   ├── api-reference.md
│   ├── cloud-providers.md
│   ├── development.md
│   └── migration-guide.md
│
├── examples/                     # Usage examples
│   ├── 01_minimal.rs
│   ├── 02_multi_cloud.rs
│   ├── 03_distributed_training.rs
│   ├── 04_managed_jobs.rs
│   ├── 05_model_serving.rs
│   └── ...
│
├── tests/                        # Integration tests
│   ├── common/
│   │   └── mod.rs
│   ├── test_launch.rs
│   ├── test_optimizer.rs
│   └── ...
│
├── scripts/                      # Build and utility scripts
│   ├── build.sh
│   ├── test.sh
│   ├── format.sh
│   └── setup-dev.sh
│
├── configs/                      # Configuration files
│   ├── default.toml
│   ├── clouds/
│   │   ├── aws.toml
│   │   ├── gcp.toml
│   │   └── azure.toml
│   └── templates/
│
└── crates/                       # Rust crates (workspace members)
    │
    ├── stix-core/               # Core orchestration engine
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── task/            # Task definition and management
    │   │   │   ├── mod.rs
    │   │   │   ├── task.rs
    │   │   │   ├── builder.rs
    │   │   │   └── validator.rs
    │   │   ├── resource/        # Resource requirements
    │   │   │   ├── mod.rs
    │   │   │   ├── resource.rs
    │   │   │   ├── requirements.rs
    │   │   │   └── accelerator.rs
    │   │   ├── dag/             # Directed Acyclic Graph
    │   │   │   ├── mod.rs
    │   │   │   ├── dag.rs
    │   │   │   ├── node.rs
    │   │   │   └── executor.rs
    │   │   ├── scheduler/       # Task scheduler
    │   │   │   ├── mod.rs
    │   │   │   ├── scheduler.rs
    │   │   │   ├── queue.rs
    │   │   │   └── allocator.rs
    │   │   ├── state/           # Global state management
    │   │   │   ├── mod.rs
    │   │   │   ├── cluster_registry.rs
    │   │   │   ├── job_registry.rs
    │   │   │   └── storage_registry.rs
    │   │   ├── error.rs         # Error types
    │   │   └── config.rs        # Core configuration
    │   └── tests/
    │
    ├── stix-optimizer/          # Cost and resource optimizer
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── optimizer.rs     # Main optimizer
    │   │   ├── cost/            # Cost optimization
    │   │   │   ├── mod.rs
    │   │   │   ├── estimator.rs
    │   │   │   ├── comparator.rs
    │   │   │   └── ranker.rs
    │   │   ├── resource/        # Resource optimization
    │   │   │   ├── mod.rs
    │   │   │   ├── selector.rs
    │   │   │   └── matcher.rs
    │   │   ├── policy/          # Optimization policies
    │   │   │   ├── mod.rs
    │   │   │   ├── cost_first.rs
    │   │   │   ├── time_first.rs
    │   │   │   └── balanced.rs
    │   │   └── graph/           # DAG optimization
    │   │       ├── mod.rs
    │   │       └── analyzer.rs
    │   └── tests/
    │
    ├── stix-clouds/             # Cloud provider implementations
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── cloud.rs         # Cloud trait
    │   │   ├── registry.rs      # Cloud registry
    │   │   ├── aws/             # AWS implementation
    │   │   │   ├── mod.rs
    │   │   │   ├── cloud.rs
    │   │   │   ├── compute.rs   # EC2 operations
    │   │   │   ├── storage.rs   # S3 operations
    │   │   │   ├── network.rs   # VPC/networking
    │   │   │   ├── iam.rs       # IAM operations
    │   │   │   └── pricing.rs   # Cost calculations
    │   │   ├── gcp/             # GCP implementation
    │   │   │   ├── mod.rs
    │   │   │   ├── cloud.rs
    │   │   │   ├── compute.rs   # Compute Engine
    │   │   │   ├── storage.rs   # GCS operations
    │   │   │   ├── network.rs
    │   │   │   ├── iam.rs
    │   │   │   └── pricing.rs
    │   │   ├── azure/           # Azure implementation
    │   │   │   ├── mod.rs
    │   │   │   ├── cloud.rs
    │   │   │   ├── compute.rs   # VM operations
    │   │   │   ├── storage.rs   # Blob storage
    │   │   │   ├── network.rs
    │   │   │   ├── identity.rs
    │   │   │   └── pricing.rs
    │   │   ├── kubernetes/      # Kubernetes backend
    │   │   │   ├── mod.rs
    │   │   │   ├── cloud.rs
    │   │   │   ├── pods.rs
    │   │   │   ├── services.rs
    │   │   │   ├── volumes.rs
    │   │   │   └── config.rs
    │   │   ├── lambda/          # Lambda Labs
    │   │   │   └── ...
    │   │   ├── paperspace/      # Paperspace
    │   │   │   └── ...
    │   │   ├── runpod/          # RunPod
    │   │   │   └── ...
    │   │   ├── vast/            # Vast.ai
    │   │   │   └── ...
    │   │   ├── cudo/            # Cudo Compute
    │   │   │   └── ...
    │   │   ├── ibm/             # IBM Cloud
    │   │   │   └── ...
    │   │   ├── oci/             # Oracle Cloud
    │   │   │   └── ...
    │   │   └── ssh/             # SSH-only cloud
    │   │       └── ...
    │   └── tests/
    │
    ├── stix-backends/           # Backend implementations
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── backend.rs       # Backend trait
    │   │   ├── registry.rs      # Backend registry
    │   │   ├── cloud_vm_ray/    # Main cloud backend
    │   │   │   ├── mod.rs
    │   │   │   ├── backend.rs
    │   │   │   ├── provisioner.rs
    │   │   │   ├── executor.rs
    │   │   │   ├── monitor.rs
    │   │   │   ├── lifecycle.rs
    │   │   │   ├── ray_cluster.rs
    │   │   │   └── sync.rs      # File syncing
    │   │   ├── local_docker/    # Local Docker backend
    │   │   │   ├── mod.rs
    │   │   │   ├── backend.rs
    │   │   │   ├── container.rs
    │   │   │   └── network.rs
    │   │   └── utils/           # Backend utilities
    │   │       ├── mod.rs
    │   │       ├── ssh.rs
    │   │       └── file_sync.rs
    │   └── tests/
    │
    ├── stix-provision/          # Provisioning system
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── provisioner.rs   # Main provisioner
    │   │   ├── common/          # Common provisioning logic
    │   │   │   ├── mod.rs
    │   │   │   ├── instance_setup.rs
    │   │   │   ├── conda.rs
    │   │   │   ├── docker.rs
    │   │   │   └── ray.rs
    │   │   ├── aws/             # AWS provisioner
    │   │   │   ├── mod.rs
    │   │   │   ├── provisioner.rs
    │   │   │   ├── ec2.rs
    │   │   │   ├── network.rs
    │   │   │   └── security.rs
    │   │   ├── gcp/             # GCP provisioner
    │   │   │   └── ...
    │   │   ├── azure/           # Azure provisioner
    │   │   │   └── ...
    │   │   ├── kubernetes/      # K8s provisioner
    │   │   │   └── ...
    │   │   └── [other clouds...]
    │   └── tests/
    │
    ├── stix-catalog/            # Resource catalog and pricing
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── catalog.rs       # Main catalog
    │   │   ├── instance_types.rs
    │   │   ├── accelerators.rs
    │   │   ├── pricing.rs
    │   │   ├── regions.rs
    │   │   ├── images.rs
    │   │   ├── aws/             # AWS catalog
    │   │   │   ├── mod.rs
    │   │   │   ├── instances.rs
    │   │   │   ├── pricing.rs
    │   │   │   └── regions.rs
    │   │   ├── gcp/             # GCP catalog
    │   │   │   └── ...
    │   │   ├── azure/           # Azure catalog
    │   │   │   └── ...
    │   │   └── [other clouds...]
    │   ├── data/                # Catalog data files
    │   │   ├── aws_instances.json
    │   │   ├── gcp_instances.json
    │   │   ├── pricing.json
    │   │   └── ...
    │   └── tests/
    │
    ├── stix-jobs/               # Managed jobs system
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── job.rs           # Job definition
    │   │   ├── queue.rs         # Job queue
    │   │   ├── scheduler.rs     # Job scheduler
    │   │   ├── controller/      # Job controller
    │   │   │   ├── mod.rs
    │   │   │   ├── controller.rs
    │   │   │   ├── monitor.rs
    │   │   │   └── recovery.rs
    │   │   ├── recovery/        # Recovery strategies
    │   │   │   ├── mod.rs
    │   │   │   ├── strategy.rs
    │   │   │   ├── retry.rs
    │   │   │   ├── failover.rs
    │   │   │   └── spot_recovery.rs
    │   │   ├── pool/            # Job pools
    │   │   │   ├── mod.rs
    │   │   │   ├── pool.rs
    │   │   │   └── allocator.rs
    │   │   └── state.rs         # Job state
    │   └── tests/
    │
    ├── stix-serve/              # Model serving (SkyServe)
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── service.rs       # Service definition
    │   │   ├── spec.rs          # Service spec
    │   │   ├── controller/      # Service controller
    │   │   │   ├── mod.rs
    │   │   │   ├── controller.rs
    │   │   │   └── orchestrator.rs
    │   │   ├── load_balancer/   # Load balancing
    │   │   │   ├── mod.rs
    │   │   │   ├── balancer.rs
    │   │   │   ├── policies/
    │   │   │   │   ├── mod.rs
    │   │   │   │   ├── round_robin.rs
    │   │   │   │   ├── least_conn.rs
    │   │   │   │   └── weighted.rs
    │   │   │   └── health.rs
    │   │   ├── autoscaler/      # Autoscaling
    │   │   │   ├── mod.rs
    │   │   │   ├── scaler.rs
    │   │   │   ├── metrics.rs
    │   │   │   └── policies.rs
    │   │   ├── replica/         # Replica management
    │   │   │   ├── mod.rs
    │   │   │   ├── manager.rs
    │   │   │   ├── lifecycle.rs
    │   │   │   └── rolling.rs
    │   │   └── endpoints.rs     # Service endpoints
    │   └── tests/
    │
    ├── stix-storage/            # Storage and data management
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── storage.rs       # Storage abstraction
    │   │   ├── volume.rs        # Volumes
    │   │   ├── bucket/          # Bucket management
    │   │   │   ├── mod.rs
    │   │   │   ├── bucket.rs
    │   │   │   ├── s3.rs        # S3 backend
    │   │   │   ├── gcs.rs       # GCS backend
    │   │   │   └── azure_blob.rs # Azure backend
    │   │   ├── transfer/        # Data transfer
    │   │   │   ├── mod.rs
    │   │   │   ├── transfer.rs
    │   │   │   ├── parallel.rs
    │   │   │   └── resume.rs
    │   │   ├── mounting/        # File mounting
    │   │   │   ├── mod.rs
    │   │   │   ├── fuse.rs
    │   │   │   └── remote_fs.rs
    │   │   └── modes.rs         # Storage modes (MOUNT, COPY, STREAM)
    │   └── tests/
    │
    ├── stix-skylet/             # Remote agent (runs on VMs)
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── main.rs          # Agent binary
    │   │   ├── daemon.rs        # Background daemon
    │   │   ├── executor/        # Task executor
    │   │   │   ├── mod.rs
    │   │   │   ├── executor.rs
    │   │   │   └── subprocess.rs
    │   │   ├── jobs/            # Job execution
    │   │   │   ├── mod.rs
    │   │   │   ├── runner.rs
    │   │   │   └── status.rs
    │   │   ├── logs/            # Log management
    │   │   │   ├── mod.rs
    │   │   │   ├── collector.rs
    │   │   │   ├── streamer.rs
    │   │   │   └── rotation.rs
    │   │   ├── autostop/        # Autostop logic
    │   │   │   ├── mod.rs
    │   │   │   ├── detector.rs
    │   │   │   └── shutdown.rs
    │   │   ├── monitor/         # Health monitoring
    │   │   │   ├── mod.rs
    │   │   │   ├── health.rs
    │   │   │   └── metrics.rs
    │   │   └── events.rs        # Event system
    │   └── tests/
    │
    ├── stix-cli/                # Command-line interface
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── main.rs          # CLI entry point
    │   │   ├── commands/        # CLI commands
    │   │   │   ├── mod.rs
    │   │   │   ├── launch.rs    # sky launch
    │   │   │   ├── exec.rs      # sky exec
    │   │   │   ├── status.rs    # sky status
    │   │   │   ├── stop.rs      # sky stop
    │   │   │   ├── start.rs     # sky start
    │   │   │   ├── down.rs      # sky down
    │   │   │   ├── queue.rs     # sky queue
    │   │   │   ├── logs.rs      # sky logs
    │   │   │   ├── autostop.rs  # sky autostop
    │   │   │   ├── cost_report.rs # sky cost-report
    │   │   │   ├── check.rs     # sky check
    │   │   │   ├── jobs/        # sky jobs subcommands
    │   │   │   │   ├── mod.rs
    │   │   │   │   ├── launch.rs
    │   │   │   │   ├── queue.rs
    │   │   │   │   ├── cancel.rs
    │   │   │   │   ├── logs.rs
    │   │   │   │   └── pool.rs
    │   │   │   ├── serve/       # sky serve subcommands
    │   │   │   │   ├── mod.rs
    │   │   │   │   ├── up.rs
    │   │   │   │   ├── down.rs
    │   │   │   │   ├── status.rs
    │   │   │   │   └── update.rs
    │   │   │   └── storage/     # sky storage subcommands
    │   │   │       ├── mod.rs
    │   │   │       ├── ls.rs
    │   │   │       └── delete.rs
    │   │   ├── output/          # Output formatting
    │   │   │   ├── mod.rs
    │   │   │   ├── table.rs
    │   │   │   ├── json.rs
    │   │   │   └── pretty.rs
    │   │   └── config.rs        # CLI config
    │   └── tests/
    │
    ├── stix-server/             # API server (optional)
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── main.rs          # Server binary
    │   │   ├── lib.rs
    │   │   ├── api/             # REST API
    │   │   │   ├── mod.rs
    │   │   │   ├── routes.rs
    │   │   │   ├── handlers/
    │   │   │   │   ├── mod.rs
    │   │   │   │   ├── tasks.rs
    │   │   │   │   ├── clusters.rs
    │   │   │   │   ├── jobs.rs
    │   │   │   │   └── storage.rs
    │   │   │   └── middleware/
    │   │   │       ├── mod.rs
    │   │   │       ├── auth.rs
    │   │   │       └── logging.rs
    │   │   ├── auth/            # Authentication
    │   │   │   ├── mod.rs
    │   │   │   ├── oauth.rs
    │   │   │   └── token.rs
    │   │   └── config.rs
    │   └── tests/
    │
    ├── stix-dashboard/          # Web UI (optional)
    │   ├── Cargo.toml
    │   ├── frontend/            # Frontend (React/Next.js)
    │   │   ├── package.json
    │   │   ├── src/
    │   │   └── public/
    │   └── backend/             # Backend API
    │       └── src/
    │
    ├── stix-config/             # Configuration management
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── config.rs        # Main config
    │   │   ├── loader.rs        # Config loading
    │   │   ├── validator.rs     # Config validation
    │   │   ├── clouds.rs        # Cloud configs
    │   │   ├── policy.rs        # Admin policies
    │   │   └── defaults.rs      # Default values
    │   └── tests/
    │
    ├── stix-auth/               # Authentication & authorization
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── credentials.rs   # Cloud credentials
    │   │   ├── oauth.rs         # OAuth flow
    │   │   ├── service_account.rs # Service accounts
    │   │   ├── validation.rs    # Credential validation
    │   │   └── refresh.rs       # Credential refresh
    │   └── tests/
    │
    ├── stix-utils/              # Shared utilities
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── command/         # Command execution
    │   │   │   ├── mod.rs
    │   │   │   ├── runner.rs
    │   │   │   ├── ssh.rs
    │   │   │   └── parallel.rs
    │   │   ├── logging/         # Logging utilities
    │   │   │   ├── mod.rs
    │   │   │   ├── logger.rs
    │   │   │   ├── formatter.rs
    │   │   │   └── aggregator.rs
    │   │   ├── network/         # Network utilities
    │   │   │   ├── mod.rs
    │   │   │   ├── http.rs
    │   │   │   └── retry.rs
    │   │   ├── fs/              # Filesystem utilities
    │   │   │   ├── mod.rs
    │   │   │   ├── sync.rs
    │   │   │   └── temp.rs
    │   │   ├── time/            # Time utilities
    │   │   │   ├── mod.rs
    │   │   │   └── timeline.rs
    │   │   ├── format/          # Output formatting
    │   │   │   ├── mod.rs
    │   │   │   ├── table.rs
    │   │   │   └── color.rs
    │   │   ├── lock/            # Locking utilities
    │   │   │   ├── mod.rs
    │   │   │   └── distributed.rs
    │   │   └── yaml.rs          # YAML utilities
    │   └── tests/
    │
    ├── stix-metrics/            # Metrics and monitoring
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── collector.rs     # Metrics collection
    │   │   ├── usage.rs         # Usage tracking
    │   │   ├── cost.rs          # Cost tracking
    │   │   └── reporter.rs      # Reporting
    │   └── tests/
    │
    ├── stix-db/                 # Database layer
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── connection.rs    # DB connection
    │   │   ├── models/          # Data models
    │   │   │   ├── mod.rs
    │   │   │   ├── cluster.rs
    │   │   │   ├── job.rs
    │   │   │   ├── storage.rs
    │   │   │   └── user.rs
    │   │   ├── migrations/      # DB migrations
    │   │   │   └── mod.rs
    │   │   └── queries/         # Query builders
    │   │       └── mod.rs
    │   └── tests/
    │
    ├── stix-sdk/                # Rust SDK (library interface)
    │   ├── Cargo.toml
    │   ├── src/
    │   │   ├── lib.rs
    │   │   ├── client.rs        # SDK client
    │   │   ├── api/             # Public API
    │   │   │   ├── mod.rs
    │   │   │   ├── launch.rs
    │   │   │   ├── exec.rs
    │   │   │   ├── status.rs
    │   │   │   └── ...
    │   │   └── compat/          # SkyPilot compatibility
    │   │       ├── mod.rs
    │   │       ├── task.rs      # SkyTask
    │   │       ├── resources.rs # SkyResources
    │   │       └── functions.rs # Top-level functions
    │   └── tests/
    │
    └── stix-macros/             # Procedural macros (if needed)
        ├── Cargo.toml
        └── src/
            └── lib.rs

```

---

## 📦 Crate Dependencies

```toml
# Root Cargo.toml
[workspace]
members = [
    "crates/stix-core",
    "crates/stix-optimizer",
    "crates/stix-clouds",
    "crates/stix-backends",
    "crates/stix-provision",
    "crates/stix-catalog",
    "crates/stix-jobs",
    "crates/stix-serve",
    "crates/stix-storage",
    "crates/stix-skylet",
    "crates/stix-cli",
    "crates/stix-server",
    "crates/stix-config",
    "crates/stix-auth",
    "crates/stix-utils",
    "crates/stix-metrics",
    "crates/stix-db",
    "crates/stix-sdk",
]

[workspace.dependencies]
# Async runtime
tokio = { version = "1.0", features = ["full"] }
async-trait = "0.1"

# Serialization
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
serde_yaml = "0.9"

# Error handling
anyhow = "1.0"
thiserror = "1.0"

# Cloud SDKs
aws-sdk-ec2 = "1.0"
aws-sdk-s3 = "1.0"
google-cloud = "0.1"
azure_core = "0.1"
kube = { version = "0.90", features = ["runtime", "derive"] }

# HTTP
reqwest = { version = "0.12", features = ["json"] }
hyper = "1.0"
axum = "0.7"

# Utilities
uuid = { version = "1.0", features = ["v4", "serde"] }
chrono = { version = "0.4", features = ["serde"] }
tracing = "0.1"
tracing-subscriber = "0.3"

# CLI
clap = { version = "4.0", features = ["derive"] }
comfy-table = "7.0"

# Database
sqlx = { version = "0.7", features = ["runtime-tokio-native-tls", "sqlite"] }

# Testing
mockall = "0.12"
```

---

## 🔄 Migration Strategy

### Phase 1: Restructure (Week 1-2)
1. Create new crate structure
2. Move existing code to appropriate crates
3. Update `Cargo.toml` dependencies
4. Ensure all crates compile independently

### Phase 2: Implement Core (Week 3-8)
1. Complete `stix-core` with full Task/Resource/DAG APIs
2. Implement `stix-optimizer` with cost optimization
3. Build `stix-backends` with CloudVmRayBackend
4. Add `stix-provision` for all clouds

### Phase 3: Advanced Features (Week 9-16)
1. Implement `stix-jobs` (managed jobs)
2. Build `stix-serve` (model serving)
3. Add `stix-storage` (data management)
4. Complete `stix-skylet` (remote agent)

### Phase 4: CLI & Tooling (Week 17-20)
1. Complete `stix-cli` with all commands
2. Build `stix-server` (API server)
3. Add `stix-dashboard` (web UI)
4. Documentation and examples

---

## 🎯 Key Improvements Over Current Structure

1. **Better Modularity**: Each crate has a single, clear responsibility
2. **Independent Testing**: Crates can be tested in isolation
3. **Parallel Development**: Multiple developers can work on different crates
4. **Feature Flags**: Easy to enable/disable cloud providers or features
5. **Clear Dependencies**: Explicit dependency graph between crates
6. **Rust Best Practices**: Following Cargo workspace conventions
7. **Maintainability**: Easier to understand and navigate
8. **Extensibility**: Easy to add new clouds, backends, or features

---

## 📚 Crate Responsibilities

| Crate | Responsibility | Dependencies |
|-------|----------------|--------------|
| `stix-core` | Core orchestration, scheduling | `stix-utils`, `stix-db` |
| `stix-optimizer` | Cost/resource optimization | `stix-core`, `stix-catalog` |
| `stix-clouds` | Cloud provider implementations | `stix-core`, `stix-auth` |
| `stix-backends` | Backend execution engines | `stix-core`, `stix-clouds`, `stix-provision` |
| `stix-provision` | VM provisioning | `stix-clouds`, `stix-auth` |
| `stix-catalog` | Resource catalog | `stix-utils` |
| `stix-jobs` | Managed jobs | `stix-core`, `stix-backends` |
| `stix-serve` | Model serving | `stix-core`, `stix-backends` |
| `stix-storage` | Storage & data | `stix-clouds` |
| `stix-skylet` | Remote agent | `stix-core`, `stix-utils` |
| `stix-cli` | Command-line interface | `stix-sdk` |
| `stix-server` | API server | `stix-sdk` |
| `stix-config` | Configuration | `stix-utils` |
| `stix-auth` | Authentication | `stix-utils` |
| `stix-utils` | Shared utilities | (no dependencies) |
| `stix-metrics` | Monitoring | `stix-core`, `stix-utils` |
| `stix-db` | Database layer | `stix-utils` |
| `stix-sdk` | Public API | All crates |

---

## 🚀 Next Steps

1. **Review and approve** this structure
2. **Create migration plan** for existing code
3. **Set up CI/CD** for multi-crate workspace
4. **Define feature flags** for optional components
5. **Write integration tests** across crates
6. **Document** each crate's public API

---

**Status**: Proposed structure ready for implementation  
**Last Updated**: November 1, 2025
