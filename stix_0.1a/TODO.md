# STIX 0.1a - Missing Features from SkyPilot Python

> Comprehensive analysis of missing features that need to be migrated from Python SkyPilot to Rust

**Date**: November 1, 2025  
**Source**: `/home/wind/ide/skypilot/recode/skypilot`  
**Target**: `/home/wind/ide/skypilot/stix_0.1a`

---

## 📋 Table of Contents

1. [Core API Functions](#1-core-api-functions)
2. [Cloud Providers](#2-cloud-providers)
3. [Backend Systems](#3-backend-systems)
4. [Provisioning](#4-provisioning)
5. [Advanced Features](#5-advanced-features)
6. [CLI Commands](#6-cli-commands)
7. [Utilities](#7-utilities)
8. [Configuration & Authentication](#8-configuration--authentication)
9. [Monitoring & Observability](#9-monitoring--observability)
10. [Data & Storage](#10-data--storage)

---

## 1. Core API Functions

### ✅ Implemented
- [x] `launch()` - Launch cluster and run task
- [x] `exec()` - Execute on existing cluster
- [x] `status()` - Get cluster status
- [x] `stop()` - Stop cluster
- [x] `start()` - Start cluster
- [x] `down()` - Terminate cluster
- [x] Basic Task API
- [x] Basic Resources API
- [x] Basic DAG support

### ❌ Missing Core Functions

#### 1.1 Optimizer
**Location**: `sky/optimizer.py` (1427 lines)
- [ ] Resource optimization engine
- [ ] Cost optimization
- [ ] Multi-cloud cost comparison
- [ ] Resource selection algorithms
- [ ] Spot instance optimization
- [ ] `OptimizeTarget` enum (COST, TIME)
- [ ] Task-to-cloud mapping optimizer
- [ ] DAG optimization for multi-task workflows

**Priority**: 🔥 HIGH - Critical for cost-effective operations

#### 1.2 Advanced Launch Options
- [ ] `dryrun` mode
- [ ] `detach_run` for background execution
- [ ] `clone_disk_from` for disk cloning
- [ ] `retry_until_up` flag
- [ ] `fast` mode for quick launches
- [ ] Cluster name auto-generation
- [ ] Idle time autostop configuration

#### 1.3 Additional Core APIs
```python
# sky/__init__.py exports
- [ ] optimize()              # Resource optimization
- [ ] reload_config()         # Reload configuration
- [ ] autostop()             # Configure autostop
- [ ] cost_report()          # Cost reporting
- [ ] endpoints()            # Service endpoints
- [ ] get()                  # API server get
- [ ] stream_and_get()       # Stream logs and get results
```

#### 1.4 Task Features
**Location**: `sky/task.py`
- [ ] Task.update_envs() - Update environment variables
- [ ] Task.update_file_mounts() - Dynamic file mount updates
- [ ] Task.update_resources() - Resource requirement updates
- [ ] Task validation and error checking
- [ ] Task serialization/deserialization
- [ ] Multi-task DAG execution
- [ ] Task dependencies
- [ ] Task retry policies
- [ ] Disk size configuration
- [ ] Image configuration per task

#### 1.5 Resource Management
**Location**: `sky/resources.py`
- [ ] Accelerator specifications (GPU/TPU)
- [ ] Instance type selection
- [ ] Region/zone specifications
- [ ] Disk tier selection (low, medium, high)
- [ ] Use spot instances flag
- [ ] Resource requirement validation
- [ ] Cost estimation per resource
- [ ] Resource comparison and ranking

---

## 2. Cloud Providers

### ✅ Implemented (Basic)
- [x] AWS (partial)
- [x] GCP (partial)
- [x] Azure (partial)
- [x] Kubernetes (partial)

### ❌ Missing Cloud Providers

#### 2.1 Additional Clouds (High Priority)
- [ ] **Lambda Cloud** (`sky/clouds/lambda_cloud.py`)
- [ ] **DigitalOcean** (`sky/clouds/do.py`)
- [ ] **Paperspace** (`sky/clouds/paperspace.py`)
- [ ] **Cudo Compute** (`sky/clouds/cudo.py`)
- [ ] **RunPod** (`sky/clouds/runpod.py`)
- [ ] **Vast.ai** (`sky/clouds/vast.py`)

#### 2.2 Enterprise & Specialized Clouds
- [ ] **IBM Cloud** (`sky/clouds/ibm.py`)
- [ ] **Oracle Cloud (OCI)** (`sky/clouds/oci.py`)
- [ ] **VMware vSphere** (`sky/clouds/vsphere.py`)
- [ ] **SCP (Samsung Cloud Platform)** (`sky/clouds/scp.py`)
- [ ] **Fluidstack** (`sky/clouds/fluidstack.py`)
- [ ] **Nebius** (`sky/clouds/nebius.py`)
- [ ] **Hyperbolic** (`sky/clouds/hyperbolic.py`)
- [ ] **Shadeform** (`sky/clouds/shadeform.py`)
- [ ] **Seeweb** (`sky/clouds/seeweb.py`)
- [ ] **PrimeIntellect** (`sky/clouds/primeintellect.py`)

#### 2.3 Cloud Features (Per Provider)
Each cloud needs:
- [ ] Instance type catalog
- [ ] Region/zone discovery
- [ ] Pricing information
- [ ] Image management
- [ ] Quota checking
- [ ] Authentication/credentials
- [ ] Network configuration
- [ ] Security group management
- [ ] IAM/permissions
- [ ] Cost estimation
- [ ] Spot instance support
- [ ] Resource limits/quotas

#### 2.4 SSH Cloud
**Location**: `sky/clouds/ssh.py`
- [ ] SSH-only cluster management
- [ ] Custom SSH node pool
- [ ] SSH authentication handling
- [ ] Remote node discovery

**Priority**: 🔥 HIGH for Lambda, Paperspace, RunPod (popular GPU clouds)

---

## 3. Backend Systems

### ✅ Implemented
- [x] Basic backend interface

### ❌ Missing Backends

#### 3.1 CloudVmRayBackend
**Location**: `sky/backends/cloud_vm_ray_backend.py` (5000+ lines)

The CORE backend that makes everything work:

- [ ] **Provisioning**
  - [ ] Cluster lifecycle management
  - [ ] Node provisioning
  - [ ] Auto-scaling
  - [ ] Spot instance handling
  - [ ] Ray cluster setup
  - [ ] Multi-node coordination

- [ ] **Execution**
  - [ ] Task execution
  - [ ] Job scheduling
  - [ ] Resource allocation
  - [ ] File syncing
  - [ ] Command execution
  - [ ] Log streaming

- [ ] **Monitoring**
  - [ ] Health checks
  - [ ] Resource monitoring
  - [ ] Job status tracking
  - [ ] Log collection
  - [ ] Failure detection

- [ ] **Teardown**
  - [ ] Graceful shutdown
  - [ ] Resource cleanup
  - [ ] Data persistence
  - [ ] Cost calculation

#### 3.2 LocalDockerBackend
**Location**: `sky/backends/local_docker_backend.py`
- [ ] Local Docker container execution
- [ ] Development/testing backend
- [ ] Container lifecycle management
- [ ] Port mapping
- [ ] Volume mounting

#### 3.3 Backend Utilities
**Location**: `sky/backends/backend_utils.py`
- [ ] SSH configuration management
- [ ] File syncing utilities
- [ ] Cluster resource info
- [ ] Backend validation
- [ ] State management

**Priority**: 🔥 CRITICAL - This is the heart of SkyPilot

---

## 4. Provisioning

### ❌ Missing Provisioning Infrastructure

#### 4.1 Core Provisioner
**Location**: `sky/provision/provisioner.py`
- [ ] VM provisioning orchestrator
- [ ] Instance creation
- [ ] Network setup
- [ ] SSH key management
- [ ] Instance configuration
- [ ] Health checking

#### 4.2 Instance Setup
**Location**: `sky/provision/instance_setup.py`
- [ ] Conda environment setup
- [ ] Docker setup
- [ ] Ray installation
- [ ] CUDA/GPU drivers
- [ ] System dependencies
- [ ] Python packages
- [ ] User scripts

#### 4.3 Cloud-Specific Provisioners
Each cloud provider needs provisioning implementation:

- [ ] **AWS** (`sky/provision/aws/`)
  - [ ] EC2 instance provisioning
  - [ ] VPC/subnet management
  - [ ] Security groups
  - [ ] IAM roles
  - [ ] EBS volumes
  
- [ ] **GCP** (`sky/provision/gcp/`)
  - [ ] Compute Engine instances
  - [ ] VPC networking
  - [ ] Firewall rules
  - [ ] Service accounts
  - [ ] Persistent disks

- [ ] **Azure** (`sky/provision/azure/`)
  - [ ] VM provisioning
  - [ ] Virtual networks
  - [ ] Network security groups
  - [ ] Managed identities
  - [ ] Managed disks

- [ ] **Kubernetes** (`sky/provision/kubernetes/`)
  - [ ] Pod management
  - [ ] Service creation
  - [ ] ConfigMaps
  - [ ] Secrets
  - [ ] Persistent volume claims
  - [ ] RBAC configuration

- [ ] **Lambda Cloud** (`sky/provision/lambda_cloud/`)
- [ ] **Paperspace** (`sky/provision/paperspace/`)
- [ ] **RunPod** (`sky/provision/runpod/`)
- [ ] **Cudo** (`sky/provision/cudo/`)
- [ ] **Vast** (`sky/provision/vast/`)
- [ ] **IBM** (`sky/provision/ibm/`)
- [ ] **OCI** (`sky/provision/oci/`)
- [ ] **vSphere** (`sky/provision/vsphere/`)
- [ ] And others...

#### 4.4 Docker Integration
**Location**: `sky/provision/docker_utils.py`
- [ ] Docker image building
- [ ] Container registry integration
- [ ] Image caching
- [ ] Multi-stage builds

**Priority**: 🔥 CRITICAL - Required for actual cluster launches

---

## 5. Advanced Features

### 5.1 Managed Jobs (SkyPilot Jobs)
**Location**: `sky/jobs/` directory

#### Core Jobs Features
- [ ] **Job Queue** (`sky/jobs/scheduler.py`)
  - [ ] Job submission queue
  - [ ] Priority scheduling
  - [ ] Resource matching
  - [ ] Automatic recovery
  
- [ ] **Job Controller** (`sky/jobs/controller.py`)
  - [ ] Controller VM management
  - [ ] Job monitoring
  - [ ] Failure recovery
  - [ ] Job lifecycle management
  
- [ ] **Recovery Strategies** (`sky/jobs/recovery_strategy.py`)
  - [ ] Automatic retry on failure
  - [ ] Spot instance recovery
  - [ ] Multi-region failover
  - [ ] StrategyExecutor

- [ ] **Job State Management** (`sky/jobs/state.py`)
  - [ ] ManagedJobStatus enum
  - [ ] Job metadata persistence
  - [ ] State transitions

- [ ] **Job APIs**
  ```rust
  // sky/jobs/__init__.py
  - [ ] jobs::launch()          // Launch managed job
  - [ ] jobs::queue()           // List jobs
  - [ ] jobs::cancel()          // Cancel job
  - [ ] jobs::tail_logs()       // Stream logs
  - [ ] jobs::download_logs()   // Download logs
  - [ ] jobs::dashboard()       // Web dashboard
  ```

- [ ] **Job Pools**
  ```rust
  - [ ] pool_apply()            // Apply pool config
  - [ ] pool_status()           // Pool status
  - [ ] pool_down()             // Tear down pool
  - [ ] pool_sync_down_logs()   // Download pool logs
  - [ ] pool_tail_logs()        // Stream pool logs
  ```

**Priority**: 🔥 HIGH - Major feature for production workloads

### 5.2 SkyServe (Model Serving)
**Location**: `sky/serve/` directory

#### Core Serve Features
- [ ] **Service Deployment** (`sky/serve/service.py`)
  - [ ] Model deployment
  - [ ] Version management
  - [ ] Rolling updates
  - [ ] A/B testing
  
- [ ] **Load Balancer** (`sky/serve/load_balancer.py`)
  - [ ] Traffic routing
  - [ ] Health checks
  - [ ] Failover
  - [ ] Load balancing policies
  
- [ ] **Autoscaling** (`sky/serve/autoscalers.py`)
  - [ ] Horizontal autoscaling
  - [ ] Metrics-based scaling
  - [ ] Scale up/down policies
  - [ ] Min/max replicas

- [ ] **Replica Management** (`sky/serve/replica_managers.py`)
  - [ ] Replica lifecycle
  - [ ] Rolling deployments
  - [ ] Health monitoring
  - [ ] Graceful shutdown

- [ ] **Service Controller** (`sky/serve/controller.py`)
  - [ ] Controller VM
  - [ ] Service orchestration
  - [ ] State management

- [ ] **Service Spec** (`sky/serve/service_spec.py`)
  - [ ] SkyServiceSpec definition
  - [ ] Service configuration
  - [ ] Validation

- [ ] **Serve APIs**
  ```rust
  - [ ] serve::up()             // Deploy service
  - [ ] serve::down()           // Tear down service
  - [ ] serve::status()         // Service status
  - [ ] serve::update()         // Update service
  - [ ] serve::tail_logs()      // Stream logs
  - [ ] serve::sync_down_logs() // Download logs
  - [ ] serve::terminate_replica() // Kill replica
  ```

**Priority**: 🔥 HIGH - Critical for ML inference

### 5.3 Storage & Data Management
**Location**: `sky/data/` and `sky/volumes/`

#### Data Features
- [ ] **Storage** (`sky/data/storage.py`)
  - [ ] S3/GCS/Azure Blob integration
  - [ ] Storage modes (MOUNT, COPY, STREAM)
  - [ ] Bucket management
  - [ ] Data sync
  
- [ ] **Data Transfer** (`sky/data/data_transfer.py`)
  - [ ] Multi-cloud data transfer
  - [ ] Parallel uploads/downloads
  - [ ] Resume on failure
  - [ ] Bandwidth optimization

- [ ] **Cloud Storage** (`sky/cloud_stores.py`)
  - [ ] Cloud-specific storage backends
  - [ ] Storage abstraction layer

- [ ] **Volumes** (`sky/volumes/volume.py`)
  - [ ] Persistent volumes
  - [ ] Volume snapshots
  - [ ] Volume cloning
  - [ ] Multi-attach support

- [ ] **Volume APIs**
  ```rust
  - [ ] volumes::apply()        // Create volume
  - [ ] volumes::ls()           // List volumes
  - [ ] volumes::delete()       // Delete volume
  ```

**Priority**: 🟡 MEDIUM - Important for data-heavy workloads

### 5.4 Skylet (Remote Agent)
**Location**: `sky/skylet/` directory

The remote agent that runs on provisioned VMs:

- [ ] **Skylet Daemon** (`sky/skylet/skylet.py`)
  - [ ] Background daemon
  - [ ] Task execution
  - [ ] Log collection
  - [ ] Health reporting
  
- [ ] **Job Library** (`sky/skylet/job_lib.py`)
  - [ ] Job execution
  - [ ] JobStatus enum
  - [ ] Job state management
  
- [ ] **Autostop** (`sky/skylet/autostop_lib.py`)
  - [ ] Idle detection
  - [ ] Automatic shutdown
  - [ ] Grace period handling
  
- [ ] **Subprocess Daemon** (`sky/skylet/subprocess_daemon.py`)
  - [ ] Long-running process management
  - [ ] Process monitoring
  
- [ ] **Log Management** (`sky/skylet/log_lib.py`)
  - [ ] Log aggregation
  - [ ] Log rotation
  - [ ] Log streaming

- [ ] **Event System** (`sky/skylet/events.py`)
  - [ ] Event handling
  - [ ] Callbacks
  - [ ] Notifications

**Priority**: 🔥 HIGH - Essential for task execution

### 5.5 Kubernetes Integration
**Location**: `sky/clouds/kubernetes.py` and `sky/provision/kubernetes/`

- [ ] Full Kubernetes backend
- [ ] Pod management
- [ ] Service exposure
- [ ] GPU node selection
- [ ] PVC management
- [ ] ConfigMap/Secret handling
- [ ] Namespace isolation
- [ ] RBAC configuration
- [ ] Ingress setup
- [ ] Helm integration

**Priority**: 🟡 MEDIUM - Important for K8s users

### 5.6 SSH Node Pools
**Location**: `sky/ssh_node_pools/`

- [ ] Custom SSH cluster management
- [ ] Node registration
- [ ] Resource allocation
- [ ] Pool server
- [ ] Health monitoring

**Priority**: 🟢 LOW - Niche feature

### 5.7 Workspaces
**Location**: `sky/workspaces/`

- [ ] Workspace management
- [ ] Multi-tenant support
- [ ] Workspace isolation
- [ ] Resource quotas
- [ ] Workspace server

**Priority**: 🟢 LOW - Advanced organizational feature

### 5.8 User Management & RBAC
**Location**: `sky/users/`

- [ ] User authentication
- [ ] Role-based access control (RBAC)
- [ ] Permission system
- [ ] Token service
- [ ] User server
- [ ] Multi-user support

**Priority**: 🟢 LOW - Enterprise feature

### 5.9 Admin Policy
**Location**: `sky/admin_policy.py`

- [ ] Policy enforcement
- [ ] Resource limits
- [ ] Cost controls
- [ ] Request validation
- [ ] Custom policies
- [ ] Policy plugins

**Priority**: 🟢 LOW - Enterprise governance

### 5.10 Dashboard (Web UI)
**Location**: `sky/dashboard/` (Next.js app)

- [ ] Web-based UI
- [ ] Cluster visualization
- [ ] Job monitoring
- [ ] Cost analytics
- [ ] Service management
- [ ] Real-time updates
- [ ] REST API
- [ ] Authentication

**Priority**: 🟡 MEDIUM - User experience enhancement

---

## 6. CLI Commands

### ✅ Implemented (Basic)
- [x] `sky launch`
- [x] `sky exec`
- [x] `sky status`
- [x] `sky stop`
- [x] `sky start`
- [x] `sky down`

### ❌ Missing CLI Commands

**Location**: `sky/client/cli/command.py` (6600+ lines)

#### 6.1 Cluster Management
```bash
- [ ] sky cost-report [--all] [--days N]    # Cost analysis
- [ ] sky queue [--skip-finished]           # Show job queue
- [ ] sky logs <cluster> [--follow]         # Stream logs
- [ ] sky autostop <cluster> [--idle-minutes N] # Configure autostop
- [ ] sky ssh <cluster> [--tmux]            # SSH into cluster
```

#### 6.2 Job Management
```bash
- [ ] sky jobs launch <yaml>                # Launch managed job
- [ ] sky jobs queue [--refresh]            # List jobs
- [ ] sky jobs cancel <job_id>              # Cancel job
- [ ] sky jobs logs <job_id> [--follow]     # Job logs
- [ ] sky jobs dashboard                    # Web dashboard
- [ ] sky jobs pool apply <yaml>            # Apply job pool
- [ ] sky jobs pool status [pool_names...]  # Pool status
- [ ] sky jobs pool down <pool_name>        # Tear down pool
```

#### 6.3 Storage Management
```bash
- [ ] sky storage ls [--verbose]            # List storage
- [ ] sky storage delete <names...> [--all] # Delete storage
```

#### 6.4 Serve Management
```bash
- [ ] sky serve up <yaml>                   # Deploy service
- [ ] sky serve down <service_name>         # Tear down service
- [ ] sky serve status [service_names...]   # Service status
- [ ] sky serve update <service_name> <yaml> # Update service
- [ ] sky serve logs <service_name>         # Service logs
```

#### 6.5 Config & Setup
```bash
- [ ] sky check [clouds...]                 # Check cloud credentials
- [ ] sky show-gpus [--cloud <cloud>]       # Show GPU availability
- [ ] sky optimize <yaml>                   # Optimize resources
```

#### 6.6 Advanced Options
- [ ] `--dryrun` flag (all commands)
- [ ] `--cloud <cloud>` filtering
- [ ] `--region <region>` specification
- [ ] `--refresh` for status updates
- [ ] `--verbose` output
- [ ] `--all-users` (multi-user)
- [ ] `--env <key=value>` environment vars
- [ ] `--gpus <spec>` GPU requirements
- [ ] `--detach-run` background execution
- [ ] `--no-setup` skip setup commands

**Priority**: 🔥 HIGH - CLI is primary user interface

---

## 7. Utilities

### ❌ Missing Utility Modules

**Location**: `sky/utils/` directory (40+ utility modules)

#### 7.1 Essential Utilities

- [ ] **Command Runner** (`command_runner.py` + `.pyi`)
  - SSH command execution
  - Command parallelization
  - Output streaming
  - Error handling

- [ ] **Cluster Utils** (`cluster_utils.py`)
  - Cluster info retrieval
  - Cluster operations
  - SSH configuration
  - Ray cluster utils

- [ ] **Config Utils** (`config_utils.py`)
  - Configuration management
  - Config validation
  - Config file handling

- [ ] **Log Utils** (`log_utils.py`)
  - Log formatting
  - Log streaming
  - Log aggregation
  - Color output

- [ ] **Timeline** (`timeline.py`)
  - Performance profiling
  - Operation timing
  - Bottleneck analysis

- [ ] **UX Utils** (`ux_utils.py`)
  - User prompts
  - Confirmation dialogs
  - Progress bars
  - Spinners

- [ ] **Rich Utils** (`rich_utils.py`)
  - Rich text formatting
  - Tables
  - Panels
  - Pretty printing

#### 7.2 Cloud-Specific Utils

- [ ] **AWS Utils** (`aws/`)
  - EC2 operations
  - S3 operations
  - IAM helpers
  - Region discovery

- [ ] **Kubernetes Utils** (`kubernetes/`)
  - K8s API wrappers
  - Pod management
  - Service creation
  - Config generation

#### 7.3 Advanced Utilities

- [ ] **DB Utils** (`db/`)
  - SQLite database
  - State persistence
  - Migration handling

- [ ] **Lock Utils** (`locks.py`, `lock_events.py`)
  - Distributed locking
  - Cluster locking
  - Concurrent operations

- [ ] **Context Utils** (`context.py`, `context_utils.py`)
  - Execution context
  - Context managers
  - Thread-local state

- [ ] **Resource Checker** (`resource_checker.py`)
  - Resource availability
  - Quota checking
  - Region validation

- [ ] **Validator** (`validator.py`)
  - Input validation
  - Schema validation
  - Type checking

- [ ] **Subprocess Utils** (`subprocess_utils.py`)
  - Safe subprocess execution
  - Timeout handling
  - Output capture

- [ ] **Volume Utils** (`volume.py`)
  - Volume operations
  - Mount management

- [ ] **YAML Utils** (`yaml_utils.py`)
  - YAML parsing
  - Schema validation
  - Template rendering

- [ ] **Git Utils** (`git.py`)
  - Git integration
  - Repo cloning
  - Version detection

- [ ] **Auth Utils** (`auth_utils.py`)
  - Authentication helpers
  - Token management

- [ ] **Message Utils** (`message_utils.py`)
  - User messaging
  - Error messages
  - Warnings

- [ ] **Perf Utils** (`perf_utils.py`)
  - Performance measurement
  - Benchmarking

**Priority**: 🟡 MEDIUM - Required for full feature parity

---

## 8. Configuration & Authentication

### ❌ Missing Configuration Systems

#### 8.1 SkyPilot Config
**Location**: `sky/skypilot_config.py`

- [ ] Global configuration
- [ ] Cloud-specific settings
- [ ] Resource defaults
- [ ] Policy settings
- [ ] Feature flags

#### 8.2 Authentication
**Location**: `sky/authentication.py` and `sky/client/`

- [ ] Cloud credential management
- [ ] Multi-cloud authentication
- [ ] OAuth support (`oauth.py`)
- [ ] Service account auth (`service_account_auth.py`)
- [ ] Credential validation
- [ ] Credential refresh

#### 8.3 Global State
**Location**: `sky/global_user_state.py`

- [ ] User state persistence
- [ ] Cluster registry
- [ ] Job history
- [ ] Storage registry
- [ ] Config cache

**Priority**: 🔥 HIGH - Required for multi-cloud operations

---

## 9. Monitoring & Observability

### ❌ Missing Monitoring Features

#### 9.1 Logging
**Location**: `sky/sky_logging.py` and `sky/logs/`

- [ ] Structured logging
- [ ] Log aggregation
- [ ] Cloud-specific log fetchers:
  - [ ] AWS CloudWatch (`logs/aws.py`)
  - [ ] GCP Cloud Logging (`logs/gcp.py`)
  - [ ] Log agent (`logs/agent.py`)

#### 9.2 Metrics
**Location**: `sky/metrics/`

- [ ] Usage metrics
- [ ] Performance metrics
- [ ] Cost metrics
- [ ] Telemetry (opt-in)

#### 9.3 Status & Health
**Location**: `sky/utils/status_lib.py`

- [ ] ClusterStatus enum
- [ ] Health check system
- [ ] Status transitions
- [ ] Failure detection

**Priority**: 🟡 MEDIUM - Important for production use

---

## 10. Data & Storage

### ❌ Missing Data Features

#### 10.1 Storage Abstraction
**Location**: `sky/data/`

- [ ] **Storage** (`storage.py`)
  - Multi-cloud storage
  - Storage modes (MOUNT, COPY, STREAM)
  - Bucket lifecycle
  
- [ ] **Data Transfer** (`data_transfer.py`)
  - Parallel transfers
  - Resume on failure
  - Progress tracking
  
- [ ] **Data Utils** (`data_utils.py`)
  - Data validation
  - Path resolution
  - URI parsing

- [ ] **Mounting Utils** (`mounting_utils.py`)
  - FUSE mounting
  - Remote FS access

#### 10.2 Cloud Storage
**Location**: `sky/cloud_stores.py`

- [ ] S3 backend
- [ ] GCS backend
- [ ] Azure Blob backend
- [ ] Storage abstraction

**Priority**: 🟡 MEDIUM - Critical for data pipelines

---

## 11. Catalog & Discovery

### ❌ Missing Catalog System

**Location**: `sky/catalog/` directory (25+ files)

#### 11.1 Catalog Infrastructure
- [ ] **Common catalog** (`common.py`)
- [ ] **Config** (`config.py`)
- [ ] **API** (`__init__.py`)
  - [ ] list_accelerators()
  - [ ] list_instance_types()
  - [ ] get_pricing()

#### 11.2 Per-Cloud Catalogs
- [ ] **AWS** (`aws_catalog.py`)
- [ ] **GCP** (`gcp_catalog.py`)
- [ ] **Azure** (`azure_catalog.py`)
- [ ] **Kubernetes** (`kubernetes_catalog.py`)
- [ ] **Lambda** (`lambda_catalog.py`)
- [ ] **Paperspace** (`paperspace_catalog.py`)
- [ ] **RunPod** (`runpod_catalog.py`)
- [ ] **Cudo** (`cudo_catalog.py`)
- [ ] **Vast** (`vast_catalog.py`)
- [ ] **IBM** (`ibm_catalog.py`)
- [ ] **OCI** (`oci_catalog.py`)
- [ ] **vSphere** (`vsphere_catalog.py`)
- [ ] **SSH** (`ssh_catalog.py`)
- [ ] And 10+ more...

#### 11.3 Data Fetchers
**Location**: `sky/catalog/data_fetchers/`

Scripts to fetch and update pricing/instance data:
- [ ] `fetch_aws.py`
- [ ] `fetch_gcp.py`
- [ ] `fetch_azure.py`
- [ ] `fetch_lambda_cloud.py`
- [ ] `fetch_*.py` for each cloud
- [ ] `analyze.py` - Data analysis

**Priority**: 🔥 HIGH - Required for cost optimization

---

## 12. Adaptors (Cloud SDKs)

### ❌ Missing Cloud SDK Adaptors

**Location**: `sky/adaptors/` directory

Lazy-loading wrappers for cloud SDKs:

- [ ] **AWS** (`aws.py`) - boto3
- [ ] **GCP** (`gcp.py`) - google-cloud
- [ ] **Azure** (`azure.py`) - azure-sdk
- [ ] **Kubernetes** (`kubernetes.py`) - kubernetes client
- [ ] **Docker** (`docker.py`) - docker SDK
- [ ] **Lambda** (`lambda_cloud.py`)
- [ ] **IBM** (`ibm.py`)
- [ ] **OCI** (`oci.py`)
- [ ] **RunPod** (`runpod.py`)
- [ ] **Vast** (`vast.py`)
- [ ] **Cudo** (`cudo.py`)
- [ ] **Nebius** (`nebius.py`)
- [ ] **Hyperbolic** (`hyperbolic.py`)
- [ ] **Shadeform** (`shadeform.py`)
- [ ] **vSphere** (`vsphere.py`)
- [ ] **Seeweb** (`seeweb.py`)
- [ ] **PrimeIntellect** (`primeintellect.py`)
- [ ] **Cloudflare** (`cloudflare.py`)
- [ ] **Common** (`common.py`) - LazyImport base

**Priority**: 🔥 HIGH - Required for cloud operations

---

## 13. Schemas & Models

### ❌ Missing Data Models

#### 13.1 Core Models
**Location**: `sky/models.py`

- [ ] ClusterConfig
- [ ] NodeConfig
- [ ] TaskConfig
- [ ] ResourceConfig
- [ ] StorageConfig

#### 13.2 API Schemas
**Location**: `sky/schemas/` (if exists)

- [ ] Request schemas
- [ ] Response schemas
- [ ] Validation schemas

**Priority**: 🟡 MEDIUM

---

## 14. Server Components

### ❌ Missing Server Infrastructure

**Location**: `sky/server/` directory

#### 14.1 API Server
- [ ] REST API server
- [ ] Request handling
- [ ] Response formatting
- [ ] Error handling
- [ ] Authentication
- [ ] Rate limiting

#### 14.2 Server Modules
- [ ] Common server utilities
- [ ] Constants
- [ ] Request models
- [ ] Response models

**Priority**: 🟢 LOW - Optional API server

---

## 15. Testing Infrastructure

### ❌ Missing Test Framework

**Location**: `tests/` directory in root

Full test coverage needed:
- [ ] Unit tests for all modules
- [ ] Integration tests
- [ ] End-to-end tests
- [ ] Cloud-specific tests
- [ ] Mock cloud providers
- [ ] Test fixtures
- [ ] CI/CD integration

**Priority**: 🔥 HIGH - Essential for reliability

---

## 16. Documentation & Examples

### ❌ Missing Documentation

**Location**: `docs/` directory

- [ ] API documentation
- [ ] User guides
- [ ] Cloud setup guides
- [ ] Troubleshooting
- [ ] Best practices
- [ ] Architecture docs

### ❌ Missing Examples

**Location**: `examples/` directory

Many YAML examples in Python SkyPilot:
- [ ] Advanced multi-cloud examples
- [ ] Distributed training examples
- [ ] Model serving examples
- [ ] Data pipeline examples
- [ ] Cost optimization examples

**Priority**: 🟡 MEDIUM - User onboarding

---

## 17. Build & Deployment

### ❌ Missing Infrastructure

- [ ] **Docker Images**
  - [ ] Dockerfile for controller
  - [ ] Dockerfile_k8s
  - [ ] Dockerfile_k8s_gpu
  - [ ] Multi-arch builds

- [ ] **CI/CD**
  - [ ] GitHub Actions (.github/workflows/)
  - [ ] Pre-commit hooks (.pre-commit-config.yaml)
  - [ ] Linting (pylint, etc.)
  - [ ] Format checking

- [ ] **Packaging**
  - [ ] Cargo workspaces
  - [ ] Feature flags
  - [ ] Release automation

**Priority**: 🟡 MEDIUM

---

## 📊 Summary Statistics

### Lines of Code Analysis
```
Python SkyPilot (recode/skypilot/sky/):
- Total Files: 896+ Python files
- Estimated Lines: ~150,000+ LOC
- Core modules: ~50,000 LOC
- Cloud providers: ~40,000 LOC
- Backends: ~20,000 LOC
- Utilities: ~20,000 LOC
- Features (jobs/serve): ~20,000 LOC
```

### Current Rust Implementation (stix_0.1a)
```
Estimated Coverage: ~10-15% of full SkyPilot
- Basic task/resource APIs ✅
- Simple launch/exec/status ✅
- Partial cloud support ✅
- Missing: 85% of features ❌
```

---

## 🎯 Implementation Priority Matrix

### Phase 1: Critical Core (0-3 months)
1. ✅ Basic Task/Resource APIs (DONE)
2. 🔥 CloudVmRayBackend (CRITICAL)
3. 🔥 Provisioning infrastructure (CRITICAL)
4. 🔥 Optimizer (CRITICAL)
5. 🔥 CLI commands (HIGH)
6. 🔥 Configuration & auth (HIGH)
7. 🔥 Catalog system (HIGH)

### Phase 2: Cloud Expansion (3-6 months)
1. Lambda, Paperspace, RunPod clouds
2. Full AWS/GCP/Azure provisioning
3. Kubernetes backend
4. Command runner & utilities
5. Storage abstraction
6. Adaptors for cloud SDKs

### Phase 3: Advanced Features (6-12 months)
1. Managed Jobs system
2. SkyServe (model serving)
3. Skylet agent
4. Data transfer system
5. Volumes management
6. Autoscaling

### Phase 4: Enterprise & Polish (12+ months)
1. User management & RBAC
2. Admin policies
3. Workspaces
4. Dashboard UI
5. SSH node pools
6. Full test coverage
7. Documentation

---

## 🏗️ Recommended Folder Structure

See separate `FOLDER_STRUCTURE.md` for detailed new architecture.

---

## 📝 Notes

1. **Backwards Compatibility**: Maintain 1:1 API compatibility with Python SkyPilot where possible
2. **Rust Idioms**: Use Rust best practices (async/await, Result types, strong typing)
3. **Performance**: Leverage Rust's performance for optimization algorithms
4. **Safety**: Use Rust's safety guarantees for concurrent operations
5. **Testing**: Write tests alongside implementation
6. **Documentation**: Document all public APIs with rustdoc

---

## 🔗 References

- **Python SkyPilot**: `/home/wind/ide/skypilot/recode/skypilot`
- **Current Rust**: `/home/wind/ide/skypilot/stix_0.1a`
- **SkyPilot Docs**: https://skypilot.readthedocs.io/
- **SkyPilot GitHub**: https://github.com/skypilot-org/skypilot

---

**Last Updated**: November 1, 2025  
**Status**: Initial analysis complete  
**Next Steps**: Review and prioritize implementation order
