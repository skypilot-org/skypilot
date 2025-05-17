#!/bin/bash
# ssh-tunnel.sh - Fast SSH tunnel script for Kubernetes API access
# Used as kubectl exec credential plugin to establish SSH tunnel on demand

# Usage: ssh-tunnel.sh --host HOST [--user USER] [--use-ssh-config] [--ssh-key KEY] [--context CONTEXT] [--port PORT]

# Parse arguments
USE_SSH_CONFIG=0
SSH_KEY=""
CONTEXT=""
HOST=""
USER=""
PORT=6443  # Default port if not specified

while [[ $# -gt 0 ]]; do
  case $1 in
    --use-ssh-config)
      USE_SSH_CONFIG=1
      shift
      ;;
    --ssh-key)
      SSH_KEY="$2"
      shift 2
      ;;
    --context)
      CONTEXT="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --user)
      USER="$2"
      shift 2
      ;;
    *)
      echo "Unknown parameter: $1" >&2
      exit 1
      ;;
  esac
done

# Validate required parameters
if [[ -z "$HOST" ]]; then
  echo "Error: --host parameter is required" >&2
  exit 1
fi

# Setup directories
TUNNEL_DIR="$HOME/.sky/tunnel"
mkdir -p "$TUNNEL_DIR"

# Get context name for PID file
if [[ -z "$CONTEXT" ]]; then
  CONTEXT="default"
fi

PID_FILE="$TUNNEL_DIR/$CONTEXT-tunnel.pid"
LOG_FILE="$TUNNEL_DIR/$CONTEXT-tunnel.log"
LOCK_FILE="$TUNNEL_DIR/$CONTEXT-tunnel.lock"

# Check if specified port is already in use (tunnel may be running)
if nc -z localhost "$PORT" 2>/dev/null; then
  # Port is in use, might be our tunnel
  echo "Port $PORT already in use" >> "$LOG_FILE"
  
  # Return valid credential format for kubectl
  echo '{"apiVersion":"client.authentication.k8s.io/v1beta1","kind":"ExecCredential","status":{"token":"k8s-ssh-tunnel-token"}}'
  exit 0
fi

# Try to acquire lock to avoid race conditions when multiple processes try to start the tunnel
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  # Another process is already starting the tunnel
  echo "Another process is starting the tunnel" >> "$LOG_FILE"
  # Wait briefly for the tunnel to be established
  for i in {1..10}; do
    if nc -z localhost "$PORT" 2>/dev/null; then
      echo '{"apiVersion":"client.authentication.k8s.io/v1beta1","kind":"ExecCredential","status":{"token":"k8s-ssh-tunnel-token"}}'
      exit 0
    fi
    sleep 0.2
  done
fi

# Check if we have a PID file with running process
if [[ -f "$PID_FILE" ]]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    # Process exists but port isn't open - something's wrong, kill it
    kill "$OLD_PID" 2>/dev/null
    echo "$(date): Killed stale tunnel process $OLD_PID" >> "$LOG_FILE"
  fi
fi

# Check for autossh
if ! command -v autossh >/dev/null 2>&1; then
  echo "$(date): ERROR: autossh is not installed but required for reliable SSH tunnels" >> "$LOG_FILE"
  echo "$(date): Please install autossh:" >> "$LOG_FILE"
  echo "$(date): - On macOS: brew install autossh" >> "$LOG_FILE"
  echo "$(date): - On Ubuntu/Debian: apt-get install autossh" >> "$LOG_FILE"
  echo "$(date): - On RHEL/CentOS: yum install autossh" >> "$LOG_FILE"
  echo "$(date): Falling back to regular ssh (less reliable)" >> "$LOG_FILE"
  
  # Fall back to regular ssh
  if [[ $USE_SSH_CONFIG -eq 1 ]]; then
    SSH_CMD=(ssh -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -o "ExitOnForwardFailure=yes" -L "$PORT:localhost:6443" -N "$HOST")
  else
    SSH_CMD=(ssh -o "StrictHostKeyChecking=no" -o "IdentitiesOnly=yes" -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -o "ExitOnForwardFailure=yes" -L "$PORT:localhost:6443" -N)
    
    # Add SSH key if provided
    if [[ -n "$SSH_KEY" ]]; then
      SSH_CMD+=(-i "$SSH_KEY")
    fi
    
    # Add user@host
    SSH_CMD+=("$USER@$HOST")
  fi
else
  # Configure autossh
  if [[ $USE_SSH_CONFIG -eq 1 ]]; then
    SSH_CMD=(autossh -M 0 -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -o "ExitOnForwardFailure=yes" -L "$PORT:localhost:6443" -N "$HOST")
  else
    SSH_CMD=(autossh -M 0 -o "StrictHostKeyChecking=no" -o "IdentitiesOnly=yes" -o "ServerAliveInterval 30" -o "ServerAliveCountMax 3" -o "ExitOnForwardFailure=yes" -L "$PORT:localhost:6443" -N)
    
    # Add SSH key if provided
    if [[ -n "$SSH_KEY" ]]; then
      SSH_CMD+=(-i "$SSH_KEY")
    fi
    
    # Add user@host
    SSH_CMD+=("$USER@$HOST")
  fi
fi

# Start the tunnel in background
{
  echo "$(date): Starting SSH tunnel for port $PORT: ${SSH_CMD[*]}" >> "$LOG_FILE"
  "${SSH_CMD[@]}" >> "$LOG_FILE" 2>&1 &
  TUNNEL_PID=$!
  echo $TUNNEL_PID > "$PID_FILE"
  echo "$(date): Tunnel started with PID $TUNNEL_PID" >> "$LOG_FILE"
  
  # Wait for tunnel to establish
  for i in {1..10}; do
    if nc -z localhost "$PORT" 2>/dev/null; then
      echo "$(date): Tunnel established successfully on port $PORT" >> "$LOG_FILE"
      break
    fi
    sleep 0.2
  done
} &

# Don't wait for the background process
disown

# Attempt to wait briefly for tunnel to be ready
for i in {1..5}; do
  if nc -z localhost "$PORT" 2>/dev/null; then
    break
  fi
  sleep 0.2
done

# Return valid credential format for kubectl
echo '{"apiVersion":"client.authentication.k8s.io/v1beta1","kind":"ExecCredential","status":{"token":"k8s-ssh-tunnel-token"}}'
exit 0 