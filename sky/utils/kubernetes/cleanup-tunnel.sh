#!/bin/bash
# cleanup-tunnel.sh - Script to clean up SSH tunnels for a Kubernetes context

# Usage: cleanup-tunnel.sh CONTEXT_NAME

CONTEXT="${1:-default}"
TUNNEL_DIR="$HOME/.sky/ssh_node_pools_info"
PID_FILE="$TUNNEL_DIR/$CONTEXT-tunnel.pid"
LOG_FILE="$TUNNEL_DIR/$CONTEXT-tunnel.log"
LOCK_FILE="$TUNNEL_DIR/$CONTEXT-tunnel.lock"

# Get the port from kubeconfig if available
KUBE_PORT=$(kubectl config view --minify --context="$CONTEXT" -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null | grep -o ":[0-9]\+" | tr -d ":" || echo "")

if [[ -z "$KUBE_PORT" ]]; then
    # Default to 6443 if we can't determine the port
    KUBE_PORT=6443
    echo "$(date): Could not determine port from kubeconfig, using default port $KUBE_PORT" >> "$LOG_FILE"
else
    echo "$(date): Found port $KUBE_PORT in kubeconfig for context $CONTEXT" >> "$LOG_FILE"
fi

# Check if PID file exists
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    
    # Log the cleanup attempt
    echo "$(date): Attempting to clean up tunnel for context $CONTEXT (PID: $OLD_PID, Port: $KUBE_PORT)" >> "$LOG_FILE"
    
    # Try to kill the process
    if kill -0 "$OLD_PID" 2>/dev/null; then
        # Process exists, kill it
        kill "$OLD_PID" 2>/dev/null
        
        # Wait a moment and check if it's really gone
        sleep 1
        if kill -0 "$OLD_PID" 2>/dev/null; then
            # Still running, force kill
            kill -9 "$OLD_PID" 2>/dev/null
            echo "$(date): Forcefully terminated tunnel process $OLD_PID" >> "$LOG_FILE"
        else
            echo "$(date): Successfully terminated tunnel process $OLD_PID" >> "$LOG_FILE"
        fi
    else
        echo "$(date): No running process found with PID $OLD_PID" >> "$LOG_FILE"
    fi
    
    # Remove PID file
    rm -f "$PID_FILE"
else
    echo "$(date): No PID file found for context $CONTEXT. Nothing to clean up." >> "$LOG_FILE"
fi

# Clean up lock file if it exists
rm -f "$LOCK_FILE"

# Check if port is still in use
if nc -z localhost "$KUBE_PORT" 2>/dev/null; then
    echo "$(date): Warning: Port $KUBE_PORT is still in use after cleanup. Another process might be using it." >> "$LOG_FILE"
fi

echo "$(date): Cleanup complete for context $CONTEXT" >> "$LOG_FILE" 