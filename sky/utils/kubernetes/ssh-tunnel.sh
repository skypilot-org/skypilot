#!/bin/bash

# TODO (kyuds): remove this script after v0.13.0. Kept here for backwards compat.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/../../ssh_node_pools/deploy/tunnel/ssh-tunnel.sh" "$@"
