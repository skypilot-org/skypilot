# SkyPilot fuse-proxy

A fuse-proxy that enables SkyPilot to run FUSE in containers without root privilege and `SYS_ADMIN` capability.

## Architecture

The fuse-proxy consists of two components:

1. A client (`fusermount-shim`) that masks the `fusermount` binary to intercept `fusermount` calls;
2. A privileged server running as a DaemonSet on each Kubernetes node;

The client and server communicate via unix domain socket, so SkyPilot Pods and the DaemonSet Pods must mount a shared directory from host.

## Usage

This component can be used independently with some integration work. Here is how SkyPilot uses this:

1. Build the server image

```bash
docker build . -t fusermount-server:latest
```

2. Deploy the server as a privileged DaemonSet: [example manifest](https://raw.githubusercontent.com/skypilot-org/skypilot/master/sky/provision/kubernetes/manifests/fusermount-server-daemonset.yaml)

3. In the application Pod, mount the shared directory:

```yaml
spec:
  containers:
  - name: main
    volumeMounts:
      - mountPath: /var/run/fusermount
        name: fusermount-shared-dir
  volumes:
    - hostPath:
        path: /var/run/fusermount
        type: DirectoryOrCreate
      name: fusermount-shared-dir
```

4. Mask the `fusermount` binary in an init-container or in init script in your application container:

```bash
# Install fuse2 and fuse3
apt-get install -y fuse fuse3
# Locate the original fusermount binary.
FUSERMOUNT_PATH=$(which fusermount)
if [ -z "$FUSERMOUNT_PATH" ]; then
    echo "Error: fusermount binary not found"
    exit 1
fi
FOURSMOUNE3_PATH=$(which fusemount3)
if [ -z "$FOURSMOUNE3_PATH" ]; then
    echo "Error: fusemount3 binary not found"
    exit 1
fi
# The -original suffix is crucial: the server will enter the mnt namespace of application container and find the original fusermount binary
# by searching `fusermount-original` executable in PATH.
cp -p "$FUSERMOUNT_PATH" "${FUSERMOUNT_PATH}-original"
# Mask the fusermount/fusemount3 binary by the shim provided by fusermount-server.
ln -sf /var/run/fusermount/fusermount-shim "$FUSERMOUNT_PATH"
ln -sf /var/run/fusermount/fusermount-shim "$FOURSMOUNE3_PATH"
```

## How it Works

### Proxy Mode: fusermount-shim

1. When a FUSE mount is requested, SkyPilot starts a FUSE adapter process (e.g., gcsfuse) in the Sky container
2. The FUSE adapter executes `fusermount-shim` (instead of the regular `fusermount`)
3. `fusermount-shim` forwards the request to the privileged daemon pod
4. The daemon pod:
   - Identifies the caller's mnt namespace
   - Performs the mount operation using `nsenter` and real `fusermount` in the caller's mnt namespace
   - Returns the mounted file descriptor back to the FUSE adapter

```mermaid
sequenceDiagram
    SkyPilot->>+Fuse Adapter: sync mount
    box beige Sky Pod (non-Privileged)
    participant Fuse Adapter
    participant fusermount-shim
    end
    Fuse Adapter->>+fusermount-shim: exec
    fusermount-shim->>+shim-server: RPC
    box pink Daemon Pod (Privileged)
    participant shim-server
    participant fusermount
    end
    Note over shim-server,fusermount: Set mnt namespace
    shim-server->>fusermount: exec
    fusermount->>fusermount: mount(/dev/fuse)
    fusermount->>shim-server: send fd
    shim-server->>fusermount-shim: return fd
    fusermount-shim->>Fuse Adapter: send fd
    Fuse Adapter->>Fuse Adapter: start working
```

## Wrapper Mode: fusermount-wrapper

For FUSE adapter that use `libfuse` to mount FUSE device directly (e.g. [blobfuse](https://github.com/Azure/azure-storage-fuse) for Azure Blob Storage), `fusermount-shim` will fail to work because `libfuse` only fallback to `fusermount` when mount operation fails.
However, `open /dev/fuse` syscall also requires root privilege in container so that the mount process will fail before the fallback happens.

`fusermount-wrapper` is used in this case to:

1. Call `fusermount-server` to mount the FUSE device before starting the FUSE adapter;
2. Pass the mounted file descriptor to the FUSE adapter, `libfuse` will [discover the mounted fd and use it directly](https://github.com/libfuse/libfuse/blob/a25fb9bd49ef56a2223262784f18dd9bbc2601dc/lib/fuse_lowlevel.c#L3435).

Refer to [fusermount-wrapper](./cmd/fusermount-wrapper/main.go) for more implementation details.

## Acknowledgements

This component is inspired by [k8s-fuse-csi-plugin](https://github.com/pfnet-research/meta-fuse-csi-plugin). Kudos to the author for the great work!