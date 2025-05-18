#!/bin/bash
# ssh-tunnel.sh - Fast SSH tunnel script for Kubernetes API access
# Used as kubectl exec credential plugin to establish SSH tunnel on demand

# Usage: ssh-tunnel.sh --host HOST [--user USER] [--use-ssh-config] [--ssh-key KEY] [--context CONTEXT] [--port PORT] [--ttl SECONDS]

# Disable debug logging by default (set to 1 to enable debugging)
DEBUG=0

# Default time-to-live for credential in seconds
# This forces kubectl to check the tunnel status frequently
TTL_SECONDS=30

# Parse arguments
USE_SSH_CONFIG=0
SSH_KEY=""
CONTEXT=""
HOST=""
USER=""
PORT=6443  # Default port if not specified

# Log only to the log file, not to stderr
debug_log() {
  if [[ $DEBUG -eq 1 ]]; then
    local message="$(date): $1"
    echo "$message" >> "$LOG_FILE"
  fi
}

# Generate expiration timestamp for credential
generate_expiration_timestamp() {
  # Try macOS date format first, fallback to Linux format
  date -u -v+${TTL_SECONDS}S +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -d "+${TTL_SECONDS} seconds" +"%Y-%m-%dT%H:%M:%SZ"
}

# Function to read certificate files if they exist
read_certificate_data() {
  local client_cert_file="$TUNNEL_DIR/$CONTEXT-cert.pem"
  local client_key_file="$TUNNEL_DIR/$CONTEXT-key.pem"
  local cert_data=""
  local key_data=""
  
  if [[ -f "$client_cert_file" ]]; then
    # Read the certificate file as is - it's already in PEM format
    cert_data=$(cat "$client_cert_file")
    debug_log "Found client certificate data for context $CONTEXT"
    
    # Log the first and last few characters to verify PEM format
    local cert_start=$(head -1 "$client_cert_file")
    local cert_end=$(tail -1 "$client_cert_file")
    debug_log "Certificate starts with: $cert_start"
    debug_log "Certificate ends with: $cert_end"
    
    # Check if it has proper PEM format
    if ! grep -q "BEGIN CERTIFICATE" "$client_cert_file" || ! grep -q "END CERTIFICATE" "$client_cert_file"; then
      debug_log "WARNING: Certificate file may not be in proper PEM format"
      # Try to fix it if needed
      if ! grep -q "BEGIN CERTIFICATE" "$client_cert_file"; then
        echo "-----BEGIN CERTIFICATE-----" > "$client_cert_file.fixed"
        cat "$client_cert_file" >> "$client_cert_file.fixed"
        echo "-----END CERTIFICATE-----" >> "$client_cert_file.fixed"
        mv "$client_cert_file.fixed" "$client_cert_file"
        cert_data=$(cat "$client_cert_file")
        debug_log "Fixed certificate format by adding BEGIN/END markers"
      fi
    fi
  fi
  
  if [[ -f "$client_key_file" ]]; then
    # Read the key file as is - it's already in PEM format
    key_data=$(cat "$client_key_file")
    debug_log "Found client key data for context $CONTEXT"
    
    # Log the first and last few characters to verify PEM format
    local key_start=$(head -1 "$client_key_file")
    local key_end=$(tail -1 "$client_key_file")
    debug_log "Key starts with: $key_start"
    debug_log "Key ends with: $key_end"
    
    # Check if it has proper PEM format
    if ! grep -q "BEGIN" "$client_key_file" || ! grep -q "END" "$client_key_file"; then
      debug_log "WARNING: Key file may not be in proper PEM format"
      # Try to fix it if needed
      if ! grep -q "BEGIN" "$client_key_file"; then
        echo "-----BEGIN PRIVATE KEY-----" > "$client_key_file.fixed"
        cat "$client_key_file" >> "$client_key_file.fixed"
        echo "-----END PRIVATE KEY-----" >> "$client_key_file.fixed"
        mv "$client_key_file.fixed" "$client_key_file"
        key_data=$(cat "$client_key_file")
        debug_log "Fixed key format by adding BEGIN/END markers"
      fi
    fi
  fi
  
  echo "$cert_data:$key_data"
}

# Function to generate credentials JSON
generate_credentials_json() {
  local expiration_time=$1
  local cert_bundle=$(read_certificate_data)
  local client_cert_data=${cert_bundle%:*}
  local client_key_data=${cert_bundle#*:}
  
  if [[ -n "$client_cert_data" && -n "$client_key_data" ]]; then
    # Debug the certificate data
    debug_log "Certificate data length: $(echo -n "$client_cert_data" | wc -c) bytes"
    debug_log "Key data length: $(echo -n "$client_key_data" | wc -c) bytes"
    
    # Check if we can create proper JSON with `jq`
    if command -v jq &>/dev/null; then
      debug_log "Using jq for JSON formatting"
      
      # Create a temporary file for the JSON output to avoid shell escaping issues
      local TEMP_JSON_FILE=$(mktemp)
      
      # Write the JSON to the temporary file using jq for proper JSON formatting
      cat > "$TEMP_JSON_FILE" << EOL
{
  "apiVersion": "client.authentication.k8s.io/v1beta1",
  "kind": "ExecCredential",
  "status": {
    "clientCertificateData": $(printf '%s' "$client_cert_data" | jq -R -s .),
    "clientKeyData": $(printf '%s' "$client_key_data" | jq -R -s .),
    "expirationTimestamp": "$expiration_time"
  }
}
EOL
      
      # Read the JSON from the file
      local json_response=$(cat "$TEMP_JSON_FILE")
      
      # Clean up
      rm -f "$TEMP_JSON_FILE"
      
      # Output the JSON
      echo "$json_response"
    else
      debug_log "jq is not available, using simpler formatting method"
      
      # Alternative approach: encode with base64 and use the token field instead
      # This works because kubectl will decode token data properly
      local combined_data=$(echo -n "${client_cert_data}:${client_key_data}" | base64 | tr -d '\n')
      
      echo "{\"apiVersion\":\"client.authentication.k8s.io/v1beta1\",\"kind\":\"ExecCredential\",\"status\":{\"token\":\"$combined_data\",\"expirationTimestamp\":\"$expiration_time\"}}"
      
      debug_log "Sent certificate data as encoded token instead of direct certificate fields"
    fi
  else
    # Fallback to token-based credential for tunnel-only authentication
    echo "{\"apiVersion\":\"client.authentication.k8s.io/v1beta1\",\"kind\":\"ExecCredential\",\"status\":{\"token\":\"k8s-ssh-tunnel-token\",\"expirationTimestamp\":\"$expiration_time\"}}"
  fi
}

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
    --ttl)
      TTL_SECONDS="$2"
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

debug_log "Starting ssh-tunnel.sh for context $CONTEXT, host $HOST, port $PORT"
debug_log "SSH Config: $USE_SSH_CONFIG, User: $USER, TTL: ${TTL_SECONDS}s"

# Check if specified port is already in use (tunnel may be running)
if nc -z localhost "$PORT" 2>/dev/null; then
  debug_log "Port $PORT already in use, checking if it's our tunnel"
  
  # Check if there's a PID file and if that process is running
  if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
      debug_log "Tunnel appears to be running with PID $OLD_PID"
    else
      debug_log "PID file exists but process $OLD_PID is not running"
    fi
  else
    debug_log "Port $PORT is in use but no PID file exists"
  fi
  
  # Generate expiration timestamp
  EXPIRATION_TIME=$(generate_expiration_timestamp)
  
  # Return valid credential format for kubectl with expiration
  generate_credentials_json "$EXPIRATION_TIME"
  exit 0
fi

# Check for flock command
if ! command -v flock >/dev/null 2>&1; then
  debug_log "flock command not available, using alternative lock mechanism"
  # Simple file-based locking
  if [ -f "$LOCK_FILE" ]; then
    lock_pid=$(cat "$LOCK_FILE" 2>/dev/null)
    if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
      debug_log "Another process ($lock_pid) is starting the tunnel, waiting briefly"
      # Wait briefly for the tunnel to be established
      for i in {1..10}; do
        if nc -z localhost "$PORT" 2>/dev/null; then
          debug_log "Tunnel is now active"
          
          # Generate expiration timestamp
          EXPIRATION_TIME=$(generate_expiration_timestamp)
          
          # Return valid credential format for kubectl with expiration
          generate_credentials_json "$EXPIRATION_TIME"
          exit 0
        fi
        sleep 0.2
      done
      debug_log "Waited for tunnel but port $PORT still not available"
    else
      # Stale lock file
      debug_log "Removing stale lock file"
      rm -f "$LOCK_FILE"
    fi
  fi
  # Create our lock
  echo $$ > "$LOCK_FILE"
else
  # Use flock for better locking
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    debug_log "Another process is starting the tunnel, waiting briefly"
    # Wait briefly for the tunnel to be established
    for i in {1..10}; do
      if nc -z localhost "$PORT" 2>/dev/null; then
        debug_log "Tunnel is now active"
        
        # Generate expiration timestamp
        EXPIRATION_TIME=$(generate_expiration_timestamp)
        
        # Return valid credential format for kubectl with expiration
        generate_credentials_json "$EXPIRATION_TIME"
        exit 0
      fi
      sleep 0.2
    done
    debug_log "Waited for tunnel but port $PORT still not available"
  fi
fi

# Check if we have a PID file with running process
if [[ -f "$PID_FILE" ]]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    # Process exists but port isn't open - something's wrong, kill it
    kill "$OLD_PID" 2>/dev/null
    debug_log "Killed stale tunnel process $OLD_PID"
  else
    debug_log "PID file exists but process $OLD_PID is not running anymore"
  fi
  # Remove the stale PID file
  rm -f "$PID_FILE"
fi

# Check for autossh
if ! command -v autossh >/dev/null 2>&1; then
  debug_log "WARNING: autossh is not installed but recommended for reliable SSH tunnels"
  debug_log "Install autossh: brew install autossh (macOS), apt-get install autossh (Ubuntu/Debian)"
  
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

debug_log "Starting SSH tunnel: ${SSH_CMD[*]}"

# Start the tunnel in foreground and wait for it to establish
"${SSH_CMD[@]}" >> "$LOG_FILE" 2>&1 &
TUNNEL_PID=$!

# Save PID immediately
echo $TUNNEL_PID > "$PID_FILE"
debug_log "Tunnel started with PID $TUNNEL_PID"

# Wait for tunnel to establish
tunnel_up=0
for i in {1..20}; do
  if nc -z localhost "$PORT" 2>/dev/null; then
    debug_log "Tunnel established successfully on port $PORT"
    tunnel_up=1
    break
  fi
  sleep 0.2
done

# Clean up lock file
if command -v flock >/dev/null 2>&1; then
  # Using flock
  exec 9>&- # Close file descriptor to release lock
else
  # Using simple lock
  rm -f "$LOCK_FILE"
fi

# Check if the tunnel process is still running
if ! kill -0 $TUNNEL_PID 2>/dev/null; then
  debug_log "ERROR: Tunnel process exited unexpectedly! Check logs for details"
  if [[ -f "$PID_FILE" ]]; then
    rm -f "$PID_FILE"
  fi
  # Return error in case of tunnel failure
  echo "Failed to establish SSH tunnel" >&2
  exit 1
elif [[ $tunnel_up -eq 0 ]]; then
  debug_log "WARNING: Tunnel process is running but port $PORT is not responding"
fi

# Generate expiration timestamp
EXPIRATION_TIME=$(generate_expiration_timestamp)

# Return valid credential format with certificates if available
generate_credentials_json "$EXPIRATION_TIME"
exit 0 