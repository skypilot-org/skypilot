# MLPerf Training and Inference on GKE with H100s

This guide demonstrates how to run MLPerf benchmarks on Google Kubernetes Engine (GKE) using NVIDIA H100 GPUs with GPUDirect-TCPX for maximum performance.

## Overview

MLPerf is the industry-standard benchmark suite for measuring AI training and inference performance. This implementation provides:

- **MLPerf Training**: ResNet-50 distributed training across multiple H100 nodes
- **MLPerf Inference**: Computer vision and NLP inference speed testing
- **GKE Integration**: Kubernetes-native deployment with GPUDirect-TCPX
- **H100 Optimization**: Leverages latest NVIDIA Hopper architecture features

## Prerequisites

- GKE cluster with H100 GPU nodes
- GPUDirect-TCPX enabled networking
- SkyPilot configured for Kubernetes
- UV package manager installed

## Quick Start

### 1. MLPerf Training Benchmark

Run the distributed ResNet-50 training benchmark:

```bash
sky launch examples/gcp_gpu_direct_tcpx/gke_mlperf_training.yaml
```

**Expected Results:**
- **Setup time**: 3-5 minutes (with UV)
- **Training time**: 10-15 minutes
- **Throughput**: 15,000-25,000 images/second
- **Configuration**: 2 nodes × 8 H100s = 16 H100s total

### 2. MLPerf Inference Benchmark

Run the inference speed benchmark:

```bash
sky launch examples/gcp_gpu_direct_tcpx/gke_mlperf_inference.yaml
```

**Expected Results:**
- **Setup time**: 2-3 minutes
- **Benchmark time**: 2-3 minutes
- **ResNet-50 latency**: 2-5ms per image
- **ResNet-50 throughput**: 2000+ images/second

## Performance Metrics

### Training Performance (16 H100s)

| Model | Total Batch Size | Per-GPU Batch | Throughput | Time to Accuracy |
|-------|------------------|---------------|------------|------------------|
| ResNet-50 | 4,096 | 256 | ~20,000 img/s | ~10 minutes |

### Inference Performance (Single H100)

| Model | Latency (ms) | Throughput (img/s) | Memory Usage |
|-------|--------------|-------------------|--------------|
| ResNet-50 | 2-5 | 2000+ | ~2GB |
| ResNet-101 | 5-8 | 1500+ | ~3GB |
| BERT-Base | 10-20 | 500+ seq/s | ~4GB |

## Architecture

### Training Setup
