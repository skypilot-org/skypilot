# SkyPilot Roadmap

## v0.3

### Managed Spot
- Minimize the cost of the controller
  - Support spot controller on existing cluster (or local cluster)
  - Reducing the fixed cost of the controller (e.g., allow setting controller VM type)
  - Increasing parallelism (number of concurrent jobs)
- Pushing the scale (e.g., support a high number of pending/concurrent jobs)
- Framework-specific guides to add checkpointing/reloading using SkyPilot Storage

### Smarter Optimizer
- Fine-grained optimizer: pick by cheapest zone order
- Better consider data egress time/cost
  - Consider buckets/Storage objects in file_mounts
- Optimizing the data placement for SkyPilot Storage local uploads
  - Use the optimizer to decide the bucket location

### Programmatic API
- Refactor/extend the current API to *make it easy to programmatically use SkyPilot*
- Expose core classes in docs

### More clouds
- Refactoring of interfaces to ease adding new clouds
- IBM Cloud

### On-prem
- Design for switching between cloud and on-prem
- Explore/design of "local mode" to run SkyPilot tasks locally

### Faster launching speed
- Consider a more minimal image
- Azure speed investigation

### k8s support
- Ray-on-k8s backend
  - To figure out: Launch a new k8s cluster? Launch SkyPilot Tasks to an existing k8s cluster?

### Cost: Optimization, Tracking, and Reporting
- Track and show costs related to a job/cluster
- For managed spot jobs, track and show %savings vs. on-demand
- Optimizer: take into account disk costs

### Serverless
- Design and prototype of a "serverless jobs" submission API and CLI
  - Initial use case: hundreds of hyperparameter tuning trials

### Backend
- Support heterogeneous node types in a cluster (e.g., in RL, CPU actor(s) and GPU learner(s) in the same cluster)
- Support CPUs as resource requirements
- General robustness/UX improvements
