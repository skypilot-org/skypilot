# Container Tools in SkyPilot Pods (DinD / BuildKit)

SkyPilot clusters running on Kubernetes are backed by one or more Pods.
Workflows that require container operations inside those Pods — such as
building and pushing images or launching nested containers — need an in-Pod
container tool. SkyPilot provides a built-in `container_tools` config that
automatically injects a sidecar container with the appropriate runtime.

This page describes two supported approaches and helps you choose the one
that fits your security posture and cluster capabilities.

## Approaches

|  | Docker-in-Docker (DinD) | Rootless BuildKit |
|---|---|---|
| Build & push images | Yes | Yes |
| Run containers (`docker run`) | Yes | No |
| Requires `privileged: true` | Yes | No |
| Requires Docker on K8s node | No (sidecar brings its own `dockerd`) | No |
| Security risk | Higher (container escape surface) | Lower |
| Tooling | Standard `docker` CLI | `buildctl` or `docker buildx` |

> **Tip:** Use BuildKit if you only need image build/push. Use DinD if you need full
> `docker run` capabilities.

## Option 1: Docker-in-Docker (DinD)

A `docker:dind` sidecar container starts its own isolated `dockerd`
process. The main container communicates with it over a shared Unix socket.
The host node does **not** need Docker installed — the sidecar is entirely
self-contained.

**Cluster prerequisite:** The cluster must allow pods with `privileged: true`.

### Configuration

Add the following to the task YAML's `config` field:

```yaml
config:
  kubernetes:
    container_tools:
      type: dind
```

Or apply it globally to all SkyPilot clusters in SkyPilot config:

```yaml
kubernetes:
  container_tools:
    type: dind
```

To persist the Docker cache across cluster restarts, specify a SkyPilot volume:

```yaml
config:
  kubernetes:
    container_tools:
      type: dind
      volume: my-builder-cache # SkyPilot volume name
```

> **Note:** For multi-node clusters, use a `ReadWriteMany` volume so all
> nodes can mount it simultaneously. Each pod gets its own `subPath` within
> the PVC, so a single volume can be safely shared across clusters.

See [dind_cluster.yaml](https://github.com/skypilot-org/skypilot/blob/master/examples/container_tools/dind_cluster.yaml) for a complete example.

### Launch and verify

```bash
sky launch -c dev examples/container_tools/dind_cluster.yaml

# Confirm the Docker daemon in the sidecar is reachable
sky exec dev -- docker info
```

## Option 2: Rootless BuildKit

Running [BuildKit](https://github.com/moby/buildkit) as a rootless sidecar provides
OCI-compliant image build and push without a privileged Docker daemon and
without requiring Docker on the host node.

**Limitation:** `docker run` / container execution is not supported. This
approach is suitable for build-only workflows (CI, image preparation).

### Configuration

Add the following to the task YAML's `config` field:

```yaml
config:
  kubernetes:
    container_tools:
      type: buildkit
```

Or apply it globally in SkyPilot config:

```yaml
kubernetes:
  container_tools:
    type: buildkit
```

To persist the BuildKit cache across cluster restarts:

```yaml
config:
  kubernetes:
    container_tools:
      type: buildkit
      volume: my-builder-cache # SkyPilot volume name
```

> **Note:** For multi-node clusters, use a `ReadWriteMany` volume so all
> nodes can mount it simultaneously. Each pod gets its own `subPath` within
> the PVC, so a single volume can be safely shared across clusters.

See [buildkit_cluster.yaml](https://github.com/skypilot-org/skypilot/blob/master/examples/container_tools/buildkit_cluster.yaml) for a complete example.

### Launch and verify

```bash
sky launch -c dev examples/container_tools/buildkit_cluster.yaml
# ssh to the cluster
ssh dev
```

#### Using `buildctl`

```bash
buildctl build \
  --frontend dockerfile.v0 \
  --local context=. \
  --local dockerfile=. \
  --output type=image,name=myregistry/myimage:latest,push=true
```

#### Using `docker buildx`

```bash
docker buildx build -t myregistry/myimage:latest --push .
```
