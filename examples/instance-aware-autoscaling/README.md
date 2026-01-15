# GPU Type-Aware Instance Autoscaling Example

This example demonstrates SkyServe's new GPU type-aware load balancing and autoscaling capabilities, which allow different GPU types to have different QPS targets and be scaled accordingly.

## Overview

Traditional load balancing treats all replicas equally, regardless of their GPU type. This can lead to inefficient resource utilization when you have a heterogeneous fleet with different GPU capabilities (e.g., H100, A100, A10).

This example shows how to:
- Configure different QPS targets for different GPU types
- Enable intelligent load balancing that considers GPU capabilities
- Scale replicas based on GPU-specific performance characteristics

## Configuration

The key configuration is in the `replica_policy` section and load_balancing policy

```yaml
load_balancing_policy: instance_aware_least_load
replica_policy:
  target_qps_per_replica:
    "H100:1": 2.5    # H100 can handle 2.5 QPS
    "A100:1": 1.25   # A100 can handle 1.25 QPS
    "A10:1": 0.5     # A10 can handle 0.5 QPS
```

## How It Works

### 1. GPU Type Detection
SkyServe automatically detects the GPU type of each replica and maps it to the configured QPS targets.

### 2. Intelligent Load Balancing
The `instance_aware_least_load` policy:
- Calculates normalized load for each replica (current_load / target_qps)
- Routes requests to the replica with the lowest normalized load
- Ensures fair distribution regardless of GPU type differences

### 3. Smart Autoscaling
- **GPU-Specific QPS**: `target_qps_per_replica` can now also accept a dictionary mapping GPU types to QPS values
- **Weighted Scaling**: Total QPS calculation considers the actual GPU types of running replicas
    - if Total QPS < Current QPS: the highest target_qps will be the standard
    - if Total QPS > Current QPS: instance with least target_qps will be downscaled first

## Example Scenario

With the configuration above:
- **1 H100 replica**: Provides 2.5 QPS capacity
- **1 A100 replica**: Provides 1.25 QPS capacity
- **1 A10 replica**: Provides 0.5 QPS capacity

## Usage

1. **Deploy the service**:
   ```bash
   sky serve up instance-aware_lambda.yml
   ```

2. **Send requests** to the service endpoint

3. **Check logs** of load_balancer and controller to see GPU type-aware autoscaling

## Supported Cloud / tested GPU Types
Only Lambda labs cloud has been tested

The system supports flexible GPU type matching:
- Exact matches: `A100`, `H100`, `A10`
- Config-based matches: `A100:1`, `H100:1`, `A10:1`

## Backward Compatibility

This feature is backward compatible. Configurations with "instance_aware_least_load" load balancing policy and dictionary type target_qps_pre_replica will only activate instance-aware autoscaling / load-balancing
