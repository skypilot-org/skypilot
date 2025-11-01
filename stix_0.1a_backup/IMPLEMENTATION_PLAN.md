# STIX - Implementation Plan

> Phased implementation plan for migrating SkyPilot Python features to Rust

**Date**: November 1, 2025  
**Target**: `/home/wind/ide/skypilot/stix_0.1a`  
**Timeline**: 20 weeks (5 months)

---

## 📋 Implementation Phases

### ✅ Phase 0: Planning & Setup (COMPLETED)
**Timeline**: Week 0  
**Status**: ✅ DONE

- [x] Analyze Python SkyPilot codebase
- [x] Create comprehensive TODO.md
- [x] Design improved folder structure
- [x] Document all missing features

---

## 🔥 Phase 1: Core Infrastructure (Weeks 1-4)

**Goal**: Establish solid foundation with core modules

### Week 1: Restructure & Core Setup

#### Tasks
- [ ] Create new folder structure as per `FOLDER_STRUCTURE.md`
- [ ] Set up Cargo workspace with all crates
- [ ] Migrate existing code to new structure
- [ ] Configure workspace dependencies
- [ ] Set up CI/CD pipeline

#### Crates to Create
```bash
stix-core/
stix-utils/
stix-config/
stix-auth/
stix-db/
```

#### Deliverables
- ✅ Working Cargo workspace
- ✅ All crates compile
- ✅ Basic CI/CD running
- ✅ Documentation structure

### Week 2: Enhanced Core APIs

#### Tasks
- [ ] Complete `stix-core` Task API
  - [ ] Task builder pattern
  - [ ] Task validation
  - [ ] Task serialization/deserialization
  - [ ] Retry policies
  - [ ] Multi-task DAG support
  
- [ ] Complete `stix-core` Resource API
  - [ ] Accelerator specifications
  - [ ] Instance type selection
  - [ ] Region/zone specs
  - [ ] Disk tier selection
  - [ ] Spot instance flags
  - [ ] Resource validation

- [ ] Enhanced DAG support
  - [ ] DAG validation
  - [ ] Dependency resolution
  - [ ] Parallel execution
  - [ ] Error propagation

#### Deliverables
- ✅ Complete Task/Resource APIs
- ✅ DAG executor
- ✅ Unit tests for core

### Week 3: Utilities & Authentication

#### Tasks
- [ ] **stix-utils**: Command runner
  - [ ] SSH command execution
  - [ ] Parallel execution
  - [ ] Output streaming
  - [ ] Error handling
  
- [ ] **stix-utils**: Logging
  - [ ] Structured logging
  - [ ] Log streaming
  - [ ] Color output
  - [ ] Log aggregation

- [ ] **stix-auth**: Credential management
  - [ ] AWS credentials
  - [ ] GCP credentials
  - [ ] Azure credentials
  - [ ] Kubernetes config
  - [ ] Credential validation
  - [ ] Refresh logic

- [ ] **stix-config**: Configuration system
  - [ ] Config loading (TOML/YAML)
  - [ ] Cloud-specific configs
  - [ ] Validation
  - [ ] Defaults

#### Deliverables
- ✅ Command runner working
- ✅ Auth for AWS/GCP/Azure
- ✅ Config system
- ✅ Unit tests

### Week 4: Database & State Management

#### Tasks
- [ ] **stix-db**: SQLite database setup
  - [ ] Schema design
  - [ ] Migrations
  - [ ] Query builders
  - [ ] Models (Cluster, Job, Storage)

- [ ] **stix-core**: Global state
  - [ ] Cluster registry
  - [ ] Job registry
  - [ ] Storage registry
  - [ ] State persistence
  - [ ] State transitions

#### Deliverables
- ✅ Working database layer
- ✅ State persistence
- ✅ Registry APIs
- ✅ Integration tests

---

## 🔥 Phase 2: Optimizer & Catalog (Weeks 5-6)

**Goal**: Resource optimization and pricing

### Week 5: Resource Catalog

#### Tasks
- [ ] **stix-catalog**: Core catalog
  - [ ] Instance types database
  - [ ] Accelerator catalog
  - [ ] Region/zone discovery
  - [ ] Image catalog

- [ ] **stix-catalog**: AWS catalog
  - [ ] EC2 instance types
  - [ ] Pricing data
  - [ ] Region availability
  - [ ] GPU/accelerator mapping

- [ ] **stix-catalog**: GCP catalog
  - [ ] Compute instance types
  - [ ] Pricing data
  - [ ] Region availability
  - [ ] TPU/GPU mapping

- [ ] **stix-catalog**: Azure catalog
  - [ ] VM sizes
  - [ ] Pricing data
  - [ ] Region availability
  - [ ] GPU mapping

#### Deliverables
- ✅ Catalog API
- ✅ AWS/GCP/Azure catalogs
- ✅ Pricing data
- ✅ Query interface

### Week 6: Cost Optimizer

#### Tasks
- [ ] **stix-optimizer**: Core optimizer
  - [ ] Optimization engine
  - [ ] Cost estimation
  - [ ] Resource ranking
  - [ ] Multi-cloud comparison

- [ ] **stix-optimizer**: Policies
  - [ ] Cost-first optimization
  - [ ] Time-first optimization
  - [ ] Balanced optimization
  - [ ] Custom policies

- [ ] **stix-optimizer**: DAG optimization
  - [ ] Multi-task optimization
  - [ ] Pipeline analysis
  - [ ] Data locality
  - [ ] Cost prediction

#### Deliverables
- ✅ Working optimizer
- ✅ Cost estimation
- ✅ Multi-cloud comparison
- ✅ Optimization tests

---

## 🔥 Phase 3: Cloud Providers (Weeks 7-10)

**Goal**: Full cloud provider support

### Week 7: AWS Cloud

#### Tasks
- [ ] **stix-clouds/aws**: Complete AWS implementation
  - [ ] EC2 operations (launch, stop, start, terminate)
  - [ ] VPC/networking setup
  - [ ] Security groups
  - [ ] IAM role management
  - [ ] EBS volume management
  - [ ] Spot instance support
  - [ ] Cost calculation

- [ ] **stix-provision/aws**: AWS provisioner
  - [ ] Instance provisioning
  - [ ] Network configuration
  - [ ] Security setup
  - [ ] SSH key management
  - [ ] Instance initialization

#### Deliverables
- ✅ Full AWS cloud support
- ✅ AWS provisioner
- ✅ Integration tests
- ✅ Examples

### Week 8: GCP Cloud

#### Tasks
- [ ] **stix-clouds/gcp**: Complete GCP implementation
  - [ ] Compute Engine operations
  - [ ] VPC networking
  - [ ] Firewall rules
  - [ ] Service accounts
  - [ ] Persistent disks
  - [ ] Preemptible instances
  - [ ] Cost calculation

- [ ] **stix-provision/gcp**: GCP provisioner
  - [ ] Instance provisioning
  - [ ] Network setup
  - [ ] Firewall config
  - [ ] Service account setup
  - [ ] Instance init

#### Deliverables
- ✅ Full GCP cloud support
- ✅ GCP provisioner
- ✅ Integration tests
- ✅ Examples

### Week 9: Azure Cloud

#### Tasks
- [ ] **stix-clouds/azure**: Complete Azure implementation
  - [ ] VM operations
  - [ ] Virtual networks
  - [ ] Network security groups
  - [ ] Managed identities
  - [ ] Managed disks
  - [ ] Spot VMs
  - [ ] Cost calculation

- [ ] **stix-provision/azure**: Azure provisioner
  - [ ] VM provisioning
  - [ ] Network setup
  - [ ] NSG configuration
  - [ ] Identity setup
  - [ ] VM initialization

#### Deliverables
- ✅ Full Azure cloud support
- ✅ Azure provisioner
- ✅ Integration tests
- ✅ Examples

### Week 10: Kubernetes & GPU Clouds

#### Tasks
- [ ] **stix-clouds/kubernetes**: Full K8s support
  - [ ] Pod management
  - [ ] Service creation
  - [ ] ConfigMaps/Secrets
  - [ ] PVC management
  - [ ] GPU node selection
  - [ ] RBAC setup

- [ ] **stix-clouds/lambda**: Lambda Labs
  - [ ] Instance management
  - [ ] GPU allocation
  - [ ] Pricing

- [ ] **stix-clouds/paperspace**: Paperspace
  - [ ] Instance management
  - [ ] GPU allocation
  - [ ] Pricing

- [ ] **stix-clouds/runpod**: RunPod
  - [ ] Instance management
  - [ ] GPU allocation
  - [ ] Pricing

#### Deliverables
- ✅ Kubernetes support
- ✅ Lambda/Paperspace/RunPod
- ✅ Integration tests

---

## 🔥 Phase 4: Backend & Provisioning (Weeks 11-12)

**Goal**: Core execution engine

### Week 11: CloudVmRayBackend (Part 1)

#### Tasks
- [ ] **stix-backends/cloud_vm_ray**: Core backend
  - [ ] Backend trait implementation
  - [ ] Cluster lifecycle
  - [ ] Node management
  - [ ] Ray cluster setup
  - [ ] Multi-node coordination

- [ ] **stix-backends/cloud_vm_ray**: Provisioning
  - [ ] VM provisioning flow
  - [ ] Network setup
  - [ ] SSH configuration
  - [ ] Health checks
  - [ ] Failure recovery

#### Deliverables
- ✅ Basic backend working
- ✅ Single-node launches
- ✅ SSH access
- ✅ Health monitoring

### Week 12: CloudVmRayBackend (Part 2)

#### Tasks
- [ ] **stix-backends/cloud_vm_ray**: Execution
  - [ ] Task execution
  - [ ] Job scheduling
  - [ ] Resource allocation
  - [ ] File syncing
  - [ ] Log streaming

- [ ] **stix-backends/cloud_vm_ray**: Monitoring
  - [ ] Resource monitoring
  - [ ] Job status tracking
  - [ ] Log collection
  - [ ] Failure detection
  - [ ] Automatic recovery

- [ ] **stix-backends/cloud_vm_ray**: Teardown
  - [ ] Graceful shutdown
  - [ ] Resource cleanup
  - [ ] Cost calculation
  - [ ] State persistence

#### Deliverables
- ✅ Full backend working
- ✅ Multi-node support
- ✅ Job execution
- ✅ End-to-end tests

---

## 🔥 Phase 5: Storage & Skylet (Weeks 13-14)

**Goal**: Data management and remote agent

### Week 13: Storage System

#### Tasks
- [ ] **stix-storage**: Core storage
  - [ ] Storage abstraction
  - [ ] S3 backend
  - [ ] GCS backend
  - [ ] Azure Blob backend
  - [ ] Storage modes (MOUNT, COPY, STREAM)

- [ ] **stix-storage**: Data transfer
  - [ ] Parallel transfers
  - [ ] Resume on failure
  - [ ] Progress tracking
  - [ ] Bandwidth optimization

- [ ] **stix-storage**: Volumes
  - [ ] Volume creation
  - [ ] Volume mounting
  - [ ] Volume snapshots
  - [ ] Volume cloning

#### Deliverables
- ✅ Storage API
- ✅ Multi-cloud storage
- ✅ Volume management
- ✅ Integration tests

### Week 14: Skylet Agent

#### Tasks
- [ ] **stix-skylet**: Remote agent
  - [ ] Background daemon
  - [ ] Task executor
  - [ ] Subprocess management
  - [ ] Log collection
  - [ ] Health reporting

- [ ] **stix-skylet**: Job execution
  - [ ] Job runner
  - [ ] Status tracking
  - [ ] Error handling
  - [ ] Resource monitoring

- [ ] **stix-skylet**: Autostop
  - [ ] Idle detection
  - [ ] Shutdown logic
  - [ ] Grace period
  - [ ] State preservation

#### Deliverables
- ✅ Working agent
- ✅ Job execution
- ✅ Autostop
- ✅ Integration tests

---

## 🔥 Phase 6: Advanced Features (Weeks 15-17)

**Goal**: Managed jobs and model serving

### Week 15: Managed Jobs (Part 1)

#### Tasks
- [ ] **stix-jobs**: Core jobs system
  - [ ] Job definition
  - [ ] Job queue
  - [ ] Job scheduler
  - [ ] State management

- [ ] **stix-jobs**: Controller
  - [ ] Controller VM management
  - [ ] Job monitoring
  - [ ] Status updates
  - [ ] Log aggregation

#### Deliverables
- ✅ Basic jobs system
- ✅ Job submission
- ✅ Job monitoring
- ✅ Tests

### Week 16: Managed Jobs (Part 2)

#### Tasks
- [ ] **stix-jobs**: Recovery strategies
  - [ ] Automatic retry
  - [ ] Spot recovery
  - [ ] Multi-region failover
  - [ ] Strategy executor

- [ ] **stix-jobs**: Job pools
  - [ ] Pool management
  - [ ] Resource allocation
  - [ ] Pool scaling
  - [ ] Pool monitoring

#### Deliverables
- ✅ Recovery system
- ✅ Job pools
- ✅ End-to-end tests
- ✅ Examples

### Week 17: Model Serving (SkyServe)

#### Tasks
- [ ] **stix-serve**: Core serving
  - [ ] Service definition
  - [ ] Service spec
  - [ ] Deployment
  - [ ] Version management

- [ ] **stix-serve**: Load balancer
  - [ ] Traffic routing
  - [ ] Health checks
  - [ ] Load balancing policies
  - [ ] Failover

- [ ] **stix-serve**: Autoscaler
  - [ ] Metrics collection
  - [ ] Scaling policies
  - [ ] Scale up/down
  - [ ] Replica management

#### Deliverables
- ✅ Serving system
- ✅ Load balancing
- ✅ Autoscaling
- ✅ Examples

---

## 🔥 Phase 7: CLI & Tooling (Weeks 18-20)

**Goal**: Complete user interface

### Week 18: CLI Commands

#### Tasks
- [ ] **stix-cli**: Core commands
  - [ ] `sky launch` (enhanced)
  - [ ] `sky exec` (enhanced)
  - [ ] `sky status` (enhanced)
  - [ ] `sky stop/start/down` (enhanced)
  - [ ] `sky queue`
  - [ ] `sky logs`
  - [ ] `sky autostop`
  - [ ] `sky cost-report`
  - [ ] `sky check`

- [ ] **stix-cli**: Jobs commands
  - [ ] `sky jobs launch`
  - [ ] `sky jobs queue`
  - [ ] `sky jobs cancel`
  - [ ] `sky jobs logs`
  - [ ] `sky jobs pool apply/status/down`

- [ ] **stix-cli**: Serve commands
  - [ ] `sky serve up`
  - [ ] `sky serve down`
  - [ ] `sky serve status`
  - [ ] `sky serve update`

- [ ] **stix-cli**: Storage commands
  - [ ] `sky storage ls`
  - [ ] `sky storage delete`

#### Deliverables
- ✅ Complete CLI
- ✅ All commands working
- ✅ Help documentation
- ✅ Examples

### Week 19: API Server & SDK

#### Tasks
- [ ] **stix-server**: REST API server
  - [ ] HTTP server
  - [ ] Route handlers
  - [ ] Authentication
  - [ ] Rate limiting
  - [ ] OpenAPI docs

- [ ] **stix-sdk**: Public Rust SDK
  - [ ] Client API
  - [ ] Async operations
  - [ ] Error handling
  - [ ] Documentation
  - [ ] Examples

#### Deliverables
- ✅ API server
- ✅ Rust SDK
- ✅ API documentation
- ✅ SDK examples

### Week 20: Polish & Documentation

#### Tasks
- [ ] **Documentation**
  - [ ] Architecture guide
  - [ ] API reference
  - [ ] User guides
  - [ ] Cloud setup guides
  - [ ] Troubleshooting

- [ ] **Examples**
  - [ ] Basic examples
  - [ ] Advanced examples
  - [ ] Multi-cloud examples
  - [ ] Distributed training
  - [ ] Model serving

- [ ] **Testing**
  - [ ] Integration test suite
  - [ ] End-to-end tests
  - [ ] Performance tests
  - [ ] Stress tests

- [ ] **CI/CD**
  - [ ] Automated testing
  - [ ] Release pipeline
  - [ ] Docker images
  - [ ] Package publishing

#### Deliverables
- ✅ Complete documentation
- ✅ Example gallery
- ✅ Full test coverage
- ✅ Release v0.1.0

---

## 📊 Progress Tracking

### Overall Progress
```
Phase 0: Planning          ████████████████████ 100% ✅
Phase 1: Core             ░░░░░░░░░░░░░░░░░░░░   0% 🔄
Phase 2: Optimizer        ░░░░░░░░░░░░░░░░░░░░   0% ⏳
Phase 3: Clouds           ░░░░░░░░░░░░░░░░░░░░   0% ⏳
Phase 4: Backend          ░░░░░░░░░░░░░░░░░░░░   0% ⏳
Phase 5: Storage          ░░░░░░░░░░░░░░░░░░░░   0% ⏳
Phase 6: Advanced         ░░░░░░░░░░░░░░░░░░░░   0% ⏳
Phase 7: CLI              ░░░░░░░░░░░░░░░░░░░░   0% ⏳

Overall: 12.5% complete
```

### Feature Completeness
```
Core APIs:          ██░░░░░░░░░░░░░░░░░░ 10%
Cloud Providers:    █░░░░░░░░░░░░░░░░░░░  5%
Backends:           ░░░░░░░░░░░░░░░░░░░░  0%
Provisioning:       ░░░░░░░░░░░░░░░░░░░░  0%
Optimizer:          ░░░░░░░░░░░░░░░░░░░░  0%
Catalog:            ░░░░░░░░░░░░░░░░░░░░  0%
Jobs:               ░░░░░░░░░░░░░░░░░░░░  0%
Serve:              ░░░░░░░░░░░░░░░░░░░░  0%
Storage:            ░░░░░░░░░░░░░░░░░░░░  0%
Skylet:             ░░░░░░░░░░░░░░░░░░░░  0%
CLI:                █░░░░░░░░░░░░░░░░░░░  5%
Utilities:          ░░░░░░░░░░░░░░░░░░░░  0%
```

---

## 🎯 Success Criteria

### Phase 1-2 Success (Weeks 1-6)
- [ ] Complete crate structure
- [ ] Core Task/Resource/DAG APIs working
- [ ] Optimizer functional
- [ ] Catalog data for AWS/GCP/Azure
- [ ] Unit tests >80% coverage

### Phase 3-4 Success (Weeks 7-12)
- [ ] AWS/GCP/Azure clouds fully working
- [ ] CloudVmRayBackend functional
- [ ] Can launch clusters on 3 clouds
- [ ] Single-node and multi-node support
- [ ] Integration tests passing

### Phase 5-6 Success (Weeks 13-17)
- [ ] Storage system working
- [ ] Skylet agent deployed
- [ ] Managed jobs functional
- [ ] Model serving working
- [ ] End-to-end workflows running

### Phase 7 Success (Weeks 18-20)
- [ ] Complete CLI
- [ ] API server running
- [ ] Full documentation
- [ ] 50+ examples
- [ ] Ready for v0.1.0 release

---

## 📝 Development Guidelines

### Code Quality
- [ ] All public APIs documented with rustdoc
- [ ] Unit tests for all modules
- [ ] Integration tests for cross-crate functionality
- [ ] Benchmarks for performance-critical paths
- [ ] Error handling with `thiserror`
- [ ] Async/await for I/O operations

### Testing Strategy
```rust
// Unit tests per crate
crates/*/tests/

// Integration tests
tests/integration/

// Performance tests
benches/

// E2E tests
tests/e2e/
```

### Documentation Requirements
- [ ] README per crate
- [ ] API docs (rustdoc)
- [ ] Architecture docs
- [ ] User guides
- [ ] Migration guides
- [ ] Troubleshooting

---

## 🚀 Quick Start (After Restructure)

```bash
# Week 1: Restructure
cd /home/wind/ide/skypilot/stix_0.1a
./scripts/restructure.sh

# Build all crates
cargo build --workspace

# Run tests
cargo test --workspace

# Build CLI
cargo build --release --bin stix

# Try it out
./target/release/stix launch examples/minimal.yaml
```

---

## 📞 Support & Communication

### Weekly Reviews
- Monday: Week planning
- Wednesday: Mid-week check-in
- Friday: Week retrospective

### Documentation
- Update TODO.md weekly
- Update progress tracking
- Document blockers

---

**Status**: Implementation ready to begin  
**Next Action**: Start Phase 1, Week 1 - Restructure  
**Last Updated**: November 1, 2025
