# SkyPilot Roadmap

This doc lists general directions of interest to facilitate community contributions. 

Note that
- This list is not meant to be comprehensive (i.e., new work items of interest may pop up)
- Even though listed under a specific version, not all items need to be completed before we ship that version (i.e., some items can go into future versions)

## v0.3

### Managed Spot
- Minimize the cost of the controller
  - Support running spot controller on an existing/local cluster
  - Reducing the fixed cost of the controller (e.g., allow setting controller VM type)
- Supporting a higher number of pending/concurrent jobs
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

### Support more clouds
- Refactoring of interfaces to ease adding new clouds
- IBM Cloud
- Explore support for low-cost clouds (e.g., lambda labs/runpod/jarvis labs)

### On-prem
- Robustify the on-prem feature
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
