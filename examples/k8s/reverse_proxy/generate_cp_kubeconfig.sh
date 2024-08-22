#!/bin/bash

set -e

# Invoke generate_kubeconfig.sh
./generate_kubeconfig.sh

# Kubeconfig generate by generate_kubeconfig.sh
KUBECONFIG_FILE="kubeconfig"

# Temporary file to hold the modified kubeconfig
TEMP_FILE=$(mktemp)

# Process the kubeconfig file
awk '
  BEGIN { in_cluster = 0 }
  /^clusters:/ { in_cluster = 1 }
  /^users:/ { in_cluster = 0 }
  in_cluster && /^ *certificate-authority-data:/ { next }
  in_cluster && /^ *server:/ {
    print "    server: https://localhost:6443"
    print "    insecure-skip-tls-verify: true"
    next
  }
  { print }
' "$KUBECONFIG_FILE" > "$TEMP_FILE"

# Replace the original kubeconfig with the modified one
mv "$TEMP_FILE" "$KUBECONFIG_FILE"

echo "Updated kubeconfig file successfully."