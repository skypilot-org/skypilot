# Cluster State: Definition & Transition Diagram

## Cluster States

* **INIT**: The provision / runtime setup has not been finished. Or, the cluster is in abnormal states, e.g. partially UP.
* **UP**: The cluster is healthy, i.e. all the nodes are UP and the ray cluster is correctly running.
* **STOPPED**: All the nodes in the cluster are STOPPED.
* **AUTOSTOPPING**: The cluster is in the process of autostopping (executing pre-stop hooks).
* **PENDING**: The cluster is pending scheduling (display only, used with job schedulers like Kueue).
* **TERMINATED**: The cluster has been terminated or does not exist. This is implicitly indicated by not appearing in the database.

## Cluster State Transition for Refreshing

### On-Demand Cluster
<!-- Image edited in https://docs.google.com/presentation/d/1PFNw6OYnr5rh4gKPvg43nmP_t1W0AXvyjszE7nNHPQ0/edit?usp=sharing -->
![Transition Diagram for On Demand](figures/cluster-state-transition.svg)

## Kubernetes-Specific State Transitions

On Kubernetes, SkyPilot maps pod phases to cluster states as follows:

| Pod Phase | Cluster State | Notes |
|-----------|---------------|-------|
| `Pending` | INIT | Pod is waiting to be scheduled or initialized |
| `Running` | UP | Pod is running and healthy |
| `Failed` | INIT | Pod has failed (OOM, eviction, etc.) |
| `Succeeded` | TERMINATED | Pod completed successfully |
| `Unknown` | TERMINATED | Pod state is unknown |

### Pod Failure Scenarios

When pods fail on Kubernetes, the cluster transitions to INIT state. Common failure scenarios include:

1. **OOM Killed**: Container exceeds memory limit
   - Container termination reason: `OOMKilled`
   - Cluster state: INIT

2. **Evicted by Kubelet**: Node resource pressure (memory, disk, ephemeral storage)
   - Pod status: `Evicted` with resource pressure message
   - Cluster state: INIT

3. **Node Drained/Deleted**: Administrator action or node failure
   - Pod events: `DeletingNode`, `NodeNotReady`, `TaintManagerEviction`
   - Cluster state: INIT (partial) or TERMINATED (all pods)

4. **Kueue Preemption**: Higher priority workload needs resources
   - Pod condition: `TerminationTarget` with Kueue preemption reason
   - Cluster state: INIT

5. **Generic Disruption**: PodDisruptionBudget or other disruptions
   - Pod condition: `DisruptionTarget`
   - Cluster state: INIT

### State Detection Logic

The state detection is implemented in `sky/provision/kubernetes/instance.py`:

- `query_instances()`: Maps pod phases to cluster states
- `_get_pod_termination_reason()`: Extracts termination reasons from pod status
- `_get_pod_missing_reason()`: Analyzes events when pods are missing

### Recovery

For clusters in INIT state due to pod failures:
1. Run `sky down <cluster>` to clean up
2. Investigate root cause via `kubectl describe pod <pod-name>`
3. Run `sky launch` to recreate with appropriate resources

For automatic recovery from preemptions and failures, use managed jobs (`sky jobs launch`).
