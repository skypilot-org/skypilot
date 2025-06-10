# DCGM Exporter Monitoring with SkyPilot API Server

This document explains how to configure the SkyPilot API server to automatically discover and monitor DCGM exporters running in external Kubernetes clusters.

## Overview

The SkyPilot API server can automatically discover DCGM exporters in Kubernetes clusters that users have configured in their SkyPilot setup. This is achieved through:

1. **HTTP Service Discovery**: The API server provides a `/api/service-discovery` endpoint that reads SkyPilot's configuration to discover allowed Kubernetes contexts
2. **Prometheus Integration**: Prometheus uses HTTP service discovery to dynamically scrape DCGM exporters
3. **Grafana Dashboards**: Pre-built dashboards for visualizing GPU metrics across multiple clusters

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│   Prometheus    │───▶│ SkyPilot API    │───▶│  External K8s    │
│                 │    │    Server       │    │   Clusters       │
└─────────────────┘    └─────────────────┘    └──────────────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌──────────────────┐
│    Grafana      │    │ SkyPilot Config │    │ DCGM Exporters  │
│   Dashboards    │    │ (~/.sky/config) │    │    (GPU Metrics) │
└─────────────────┘    └─────────────────┘    └──────────────────┘
```

## Prerequisites

### 1. DCGM Exporters in External Clusters

Each Kubernetes cluster that you want to monitor must have DCGM exporters deployed.

#### Using GPU Operator (Recommended)
https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html#common-deployment-scenarios


### 2. SkyPilot Configuration

Ensure your SkyPilot configuration includes the Kubernetes clusters you want to monitor:

```yaml
# ~/.sky/config.yaml
kubernetes:
  allowed_contexts:
    - gke_project_zone_cluster1
    - eks-cluster-2
    - my-onprem-cluster
```

## Configuration

### Deploy with Helm

```bash
helm install skypilot ./charts/skypilot \
  --set apiService.metrics.enabled=true \
  --set prometheus.enabled=true \
  --set grafana.enabled=true \
```

## How It Works

### 1. Service Discovery Process

The SkyPilot API server provides a `/api/service-discovery` endpoint that:

1. **Reads SkyPilot Config**: Uses SkyPilot's existing configuration system to find `allowed_contexts`
2. **Generates Targets**: Creates Prometheus-compatible target definitions for DCGM exporters
3. **Returns JSON**: Serves targets in HTTP service discovery format
4. **Real-time**: No caching - always returns current configuration

### 2. Prometheus Integration

Prometheus is configured with HTTP service discovery:

```yaml
# Prometheus scrape config (automatically generated)
- job_name: 'dcgm-exporter-http-sd'
  http_sd_configs:
  - url: http://skypilot-api:46580/api/service-discovery
    refresh_interval: 60s
  relabel_configs:
  - regex: __meta_sd_label_(.+)
    action: labelmap
```

### 3. Target Format

The service discovery endpoint returns targets in this format:

```json
[
  {
    "targets": ["dcgm-exporter.gpu-operator.svc.cluster.local:9400"],
    "labels": {
      "cluster": "gke_project_zone_cluster1",
      "kubernetes_namespace": "gpu-operator",
      "job": "dcgm-exporter",
      "__scheme__": "http",
      "__metrics_path__": "/metrics"
    }
  }
]
```

## Monitoring and Troubleshooting

### 1. Check Service Discovery Health

```bash
# Check if API server is running
kubectl get pods -l app=skypilot

# Test the endpoint directly
kubectl port-forward svc/skypilot 46580:46580
curl http://localhost:46580/api/service-discovery
```

### 2. Verify Prometheus Targets

1. Access Prometheus UI: `http://<prometheus-url>/targets`
2. Look for job `dcgm-exporter-http-sd`
3. Verify targets are being discovered and scraped successfully

### 3. Check Grafana Dashboards

1. Access Grafana UI
2. Look for "GPU Metrics" dashboard
3. Verify data is flowing from external clusters

### 4. Common Issues

#### No Targets Discovered
- Check SkyPilot config has `allowed_contexts` defined
- Verify API server is running and accessible
- Test the service discovery endpoint manually

#### Targets Not Scraped
- Verify DCGM exporters are running in external clusters
- Check network connectivity between Prometheus and external clusters
- Ensure DCGM exporters have correct annotations:
  ```yaml
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "9400"
    prometheus.io/path: "/metrics"
  ```

#### Authentication Issues
- Ensure API server has proper RBAC permissions
- Check if external clusters require authentication
- Verify SkyPilot contexts are valid

## Advanced Configuration

### 1. Custom Namespaces

The service discovery looks for DCGM exporters in these namespaces by default:
- `default`
- `gpu-operator` 
- `kube-system`
- `monitoring`

To customize, modify the `service_discovery` endpoint in `sky/server/server.py`.

### 2. Custom Ports

Default DCGM exporter port is 9400. If using custom ports, ensure they're reflected in:
- DCGM exporter service annotations
- Service discovery endpoint configuration

### 3. Security Considerations

- **Network Policies**: Ensure Prometheus can reach external cluster services
- **Authentication**: Consider using service accounts or tokens for cluster access
- **TLS**: Enable TLS for production deployments
- **RBAC**: Limit API server permissions to read-only cluster access

## Metrics Available

Common DCGM metrics you can monitor:

- `DCGM_FI_DEV_GPU_UTIL`: GPU utilization percentage
- `DCGM_FI_DEV_MEM_COPY_UTIL`: Memory utilization percentage  
- `DCGM_FI_DEV_POWER_USAGE`: Power consumption in watts
- `DCGM_FI_DEV_GPU_TEMP`: GPU temperature in Celsius
- `DCGM_FI_DEV_SM_CLOCK`: SM clock frequency
- `DCGM_FI_DEV_MEM_CLOCK`: Memory clock frequency

## Example Queries

```promql
# Average GPU utilization across all clusters
avg(DCGM_FI_DEV_GPU_UTIL) by (cluster, kubernetes_node_name)

# GPU memory utilization by cluster
avg(DCGM_FI_DEV_MEM_COPY_UTIL) by (cluster)

# Power consumption across all GPUs
sum(DCGM_FI_DEV_POWER_USAGE) by (cluster)

# GPU temperature alerts
DCGM_FI_DEV_GPU_TEMP > 80
``` 