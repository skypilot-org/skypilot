"""Kubernetes FUSE mounting helpers."""

import textwrap

# Shared directory to communicate with fusermount-server, refer to
# addons/fuse-proxy/README.md for more details.
FUSERMOUNT_SHARED_DIR = '/var/run/fusermount'


def get_fusermount_shim_setup_command(
        sudo_cmd: str = 'sudo',
        shared_dir: str = '$FUSERMOUNT_SHARED_DIR') -> str:
    """Return the pod-side setup command for the fusermount shim."""
    sudo = f'{sudo_cmd} ' if sudo_cmd else ''
    command = textwrap.dedent("""
        set -e
        # Mask fusermount binary before enabling SSH access
        FUSERMOUNT_PATH=$(which fusermount)
        if [ -z "$FUSERMOUNT_PATH" ]; then
            echo "Error: fusermount binary not found"
            exit 1
        fi
        __SUDO__cp -p "$FUSERMOUNT_PATH" "${FUSERMOUNT_PATH}-original"
        __SUDO__ln -sf "__SHARED_DIR__/fusermount-shim" "$FUSERMOUNT_PATH"
        # "|| true" because fusermount3 is not always available
        FUSERMOUNT3_PATH=$(which fusermount3) || true
        if [ -z "$FUSERMOUNT3_PATH" ]; then
            FUSERMOUNT3_PATH="${FUSERMOUNT_PATH}3"
        fi
        # Also mask fusermount3 for rclone and blobfuse2 (for unmount operation)
        __SUDO__ln -sf "$FUSERMOUNT_PATH" "$FUSERMOUNT3_PATH"
        # Add fusermount-wrapper to handle adapters that use libfuse directly,
        # e.g. blobfuse2.
        __SUDO__ln -sf "__SHARED_DIR__/fusermount-wrapper" /bin/fusermount-wrapper
        # Wait for the server to setup the fusermount shim binary in case:
        # 1. The server daemonset was just deployed and is still starting up.
        # 2. The node was just started and the server Pod is still starting up.
        wait_for_fusermount() {
            local timeout=60
            local start_time=$(date +%s)
            while ! command -v fusermount >/dev/null 2>&1; do
                current_time=$(date +%s)
                elapsed=$((current_time - start_time))
                if [ $elapsed -ge $timeout ]; then
                    echo "Error: fusermount not ready after $timeout seconds"
                    exit 1
                fi
                sleep 1
            done
        }
        wait_for_fusermount
        # Some distributions may mount hostPath with noexec, copy the binary
        # in this case.
        if ! fusermount -V; then
            echo "fusermount -V failed, copying fusermount-shim directly"
            __SUDO__rm -f "$FUSERMOUNT_PATH"
            __SUDO__cp -p "__SHARED_DIR__/fusermount-shim" "$FUSERMOUNT_PATH"
            __SUDO__rm -f /bin/fusermount-wrapper
            __SUDO__cp -p "__SHARED_DIR__/fusermount-wrapper" /bin/fusermount-wrapper
        fi
    """).strip()
    return command.replace('__SUDO__', sudo).replace('__SHARED_DIR__',
                                                     shared_dir)
