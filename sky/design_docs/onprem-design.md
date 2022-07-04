# Sky On-prem

## Multi-tenancy
- Every user has their own job queue.
- Every user will start their own skylet (whenever `sky launch` is first called)

## Heterogeneous Accelerator Support
- Supports different types of accelerators across nodes (internode)
- Does not support different types of accelerators within the same node (intranode)

## Miscellaneous
- `sky start/stop/autostop` is not supported.
- `sky down` is supported. The command `sky down` does terminate the cluster, but it kills all user's jobs in the cluster and removes the local cluster from `sky status`.
