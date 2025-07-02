# GPU Availability Issue Investigation Report

## Issue Summary
Users are experiencing negative GPU counts displayed in the SkyPilot dashboard infrastructure page, along with missing GPU type information despite showing quantity data.

## Root Cause Analysis

### The Problem
The issue occurs in `sky/catalog/kubernetes_catalog.py` in the `_list_accelerators()` function, specifically in lines 247-262:

```python
for pod in pods:
    # Get all the pods running on the node
    if (pod.spec.node_name == node.metadata.name and
            pod.status.phase in ['Running', 'Pending']):
        # Iterate over all the containers in the pod and sum
        # the GPU requests
        for container in pod.spec.containers:
            if container.resources.requests:
                allocated_qty += (
                    kubernetes_utils.get_node_accelerator_count(
                        container.resources.requests))

accelerators_available = accelerator_count - allocated_qty
```

### Specific Case Analysis
From the Slack thread investigation of node `computeinstance-u00j4c733hrmavgaj2`:

1. **Node GPU Resources:**
   - Total Capacity: `nvidia.com/gpu: 8`
   - Allocatable: `nvidia.com/gpu: 5` (3 GPUs reserved by system)

2. **Pod Allocation:**
   - Pod `sky-63e2-shahbuland-97e289c7-head` requests 8 GPUs
   - This pod is likely in **Pending** state because it requests more GPUs (8) than allocatable (5)
   - However, Kubernetes still assigns the pod to the node (`spec.nodeName` is set)

3. **Faulty Calculation:**
   - `accelerator_count = 5` (allocatable GPUs from `node.status.allocatable`)
   - `allocated_qty = 8` (includes the pending pod's GPU request)
   - `accelerators_available = 5 - 8 = -3` ❌

### Why This Happens
1. **Kubernetes Scheduling Behavior**: Kubernetes can assign pods to nodes even when they exceed available resources, marking them as Pending
2. **Counting Logic Bug**: The code counts GPU requests from both Running AND Pending pods assigned to a node
3. **Resource Mismatch**: Using allocatable capacity but counting requests that may exceed that capacity

## Impact Assessment

### Dashboard Display Issues
- **Negative GPU counts** shown in infrastructure page tables
- **Missing GPU type information** while showing quantities
- **Incorrect utilization calculations** affecting resource planning
- **Misleading availability information** for users

### Data Flow
1. `kubernetes_catalog.py` calculates negative availability
2. Data flows through API endpoints to dashboard
3. Dashboard displays negative values in GPU tables and utilization bars
4. Per-node GPU information also affected

## Evidence from Investigation

### Node Status Output
```bash
Capacity:
  nvidia.com/gpu: 8
Allocatable:
  nvidia.com/gpu: 5
Allocated resources:
  nvidia.com/gpu: 8
```

### Pod Status Output
```bash
NAMESPACE   NAME                            GPU
default     sky-63e2-shahbuland-97e289c7-head   8
```

This confirms a pod requesting 8 GPUs on a node with only 5 allocatable GPUs.

## Proposed Solution

### Option 1: Only Count Running Pods (Recommended)
Modify the pod counting logic to exclude Pending pods:

```python
if (pod.spec.node_name == node.metadata.name and
        pod.status.phase == 'Running'):  # Remove 'Pending'
```

**Pros:**
- Simple, targeted fix
- Reflects actual resource consumption
- Aligns with Kubernetes resource accounting best practices

**Cons:**
- May not account for resources reserved by scheduler for pending pods

### Option 2: Check Pod Schedulability
Add logic to verify if pending pods can actually be scheduled:

```python
if (pod.spec.node_name == node.metadata.name):
    if pod.status.phase == 'Running':
        # Always count running pods
        allocated_qty += gpu_request
    elif pod.status.phase == 'Pending':
        # Only count pending pods that don't exceed node capacity
        if allocated_qty + gpu_request <= accelerator_count:
            allocated_qty += gpu_request
```

**Pros:**
- More sophisticated resource tracking
- Accounts for legitimately pending pods

**Cons:**
- More complex logic
- May still have edge cases

### Option 3: Use Node Status Directly
Instead of calculating from pod requests, use Kubernetes' own accounting:

```python
# Use node.status.allocatable - node.status.allocated
allocated_from_node = node.status.allocated.get('nvidia.com/gpu', 0)
accelerators_available = accelerator_count - allocated_from_node
```

**Note:** This would require checking if Kubernetes exposes allocated resources in node status.

## Recommended Fix

**Implement Option 1** as the immediate fix:

```python
# In sky/catalog/kubernetes_catalog.py, line ~247
for pod in pods:
    # Get all the pods running on the node
    if (pod.spec.node_name == node.metadata.name and
            pod.status.phase == 'Running'):  # Changed: removed 'Pending'
        # Iterate over all the containers in the pod and sum
        # the GPU requests
        for container in pod.spec.containers:
            if container.resources.requests:
                allocated_qty += (
                    kubernetes_utils.get_node_accelerator_count(
                        container.resources.requests))
```

### Rationale
1. **Fixes immediate issue**: Eliminates negative GPU counts
2. **Semantically correct**: Only running pods actually consume resources
3. **Low risk**: Minimal code change with clear behavior
4. **Consistent**: Aligns with how most monitoring systems count resource usage

## Testing Recommendations

1. **Verify fix resolves negative counts** on the problematic node
2. **Test with various pod states**: Running, Pending, Failed, Succeeded
3. **Check dashboard displays correctly** after fix
4. **Validate GPU utilization calculations** are accurate
5. **Test with multiple GPU types** to ensure no regression

## Additional Considerations

### GPU Type Display Issue
The missing GPU type information likely stems from the same calculation producing invalid data. The fix should resolve both negative counts and missing type information.

### Future Improvements
- Add logging/metrics for pod scheduling failures
- Consider alerting when pods are pending due to insufficient GPU resources
- Implement more sophisticated resource planning features

## Files to Modify
- `sky/catalog/kubernetes_catalog.py` (line ~249)

## Related GitHub Issue/PR
This investigation relates to the hypothesis mentioned in the Slack thread about counting Pending pods in the GPU allocation calculation.