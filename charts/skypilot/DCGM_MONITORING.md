# DCGM Exporter Monitoring with SkyPilot API Server

This document explains how to configure the SkyPilot API server to automatically monitor DCGM exporters running in the same Kubernetes cluster.

## Overview

The SkyPilot API server can automatically monitor DCGM exporters deployed in the same Kubernetes cluster. This is achieved through:

1. **Kubernetes Service Discovery**: Prometheus uses Kubernetes-native service discovery to automatically find DCGM exporters
2. **Grafana Dashboards**: Pre-built dashboards for visualizing GPU metrics from the cluster

**Note**: The current implementation works within a single Kubernetes cluster where the SkyPilot API server, Prometheus, and DCGM exporters are all deployed together.

## Architecture

```
┌─────────────────┐    ┌──────────────────┐
│   Prometheus    │───▶│ DCGM Exporters  │
│                 │    │  (Same Cluster)  │
└─────────────────┘    └──────────────────┘
         │                       │
         │                       │
         ▼                       ▼
┌─────────────────┐    ┌──────────────────┐
│    Grafana      │    │   GPU Metrics    │
│   Dashboards    │    │  (Kubernetes SD) │
└─────────────────┘    └──────────────────┘
```

## Prerequisites

### 1. DCGM Exporters in the Same Cluster

The Kubernetes cluster where SkyPilot is deployed must have DCGM exporters running with proper Prometheus configuration.

#### Using GPU Operator
https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html#common-deployment-scenarios

While the GPU Operator provides DCGM exporter capabilities, **manual configuration is typically required** to enable proper Prometheus scraping.

#### Manual DCGM Exporter Configuration

After installing the GPU Operator, you may need to manually configure or deploy DCGM exporters with the correct Prometheus annotations:

```yaml
# Example DCGM exporter deployment with Prometheus annotations
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: nvidia-dcgm-exporter
  namespace: gpu-operator
spec:
  selector:
    matchLabels:
      app: nvidia-dcgm-exporter
  template:
    metadata:
      labels:
        app: nvidia-dcgm-exporter
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "9400"
        prometheus.io/path: "/metrics"
    spec:
      # ... rest of DCGM exporter configuration
```

#### Expected DCGM Exporter Configuration

Once properly configured, you should see:

**NVIDIA GPU Operator DCGM Exporter:**
- **Service Name**: `nvidia-dcgm-exporter`
- **Namespace**: `gpu-operator`
- **Port**: `9400`
- **Metrics Path**: `/metrics`
- **Prometheus Annotation**: `prometheus.io/scrape: true`

**Note**: Depending on your cloud provider or cluster setup, you may also see additional DCGM exporters (e.g., cloud provider-specific ones like `nebius-dcgm`). Prometheus will automatically discover and scrape all properly annotated DCGM exporters.

#### Verification Commands

```bash
# Check if DCGM exporters are running
kubectl get pods -A | grep dcgm

# Check DCGM exporter services and annotations
kubectl get svc -A | grep dcgm
kubectl describe svc -n gpu-operator nvidia-dcgm-exporter

# Test metrics endpoint
kubectl port-forward -n gpu-operator svc/nvidia-dcgm-exporter 9400:9400
curl http://localhost:9400/metrics
```

## Configuration

### Deploy with Helm

```bash
helm install skypilot ./charts/skypilot \
  --set apiService.metrics.enabled=true \
  --set prometheus.enabled=true \
  --set grafana.enabled=true
```

### Dashboard Configuration

The NVIDIA DCGM dashboard is automatically provisioned using Grafana's dashboard import feature:

```yaml
# In values.yaml
grafana:
  enabled: true
  dashboardProviders:
    dashboardproviders.yaml:
      apiVersion: 1
      providers:
      - name: 'default'
        orgId: 1
        folder: ''
        type: file
        disableDeletion: false
        allowUiUpdates: false
        updateIntervalSeconds: 30
        options:
          path: /var/lib/grafana/dashboards/default
```

## How It Works

### 1. Kubernetes Service Discovery

Prometheus is configured to use Kubernetes service discovery to automatically find DCGM exporters:

```yaml
# Prometheus scrape config (automatically generated)
- job_name: 'kubernetes-pods'
  kubernetes_sd_configs:
  - role: pod
  relabel_configs:
  - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
    action: keep
    regex: true
  - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
    action: replace
    target_label: __metrics_path__
    regex: (.+)
```

### 2. Dashboard Provisioning

Grafana automatically:
1. Discovers dashboards defined in charts/skypilot/manifests/
2. Uses the Prometheus datasource
