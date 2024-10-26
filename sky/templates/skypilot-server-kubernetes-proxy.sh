#!/bin/bash

# Check if all required arguments are provided
if [ "$#" -ne 4 ]; then
    echo "Usage: $0 <api_server> <context> <namespace> <pod_name>" >&2
    exit 1
fi

API_SERVER="$1"
CONTEXT="$2"
NAMESPACE="$3"
POD_NAME="$4"

# Extract host and port from API_SERVER
HOST=$(echo $API_SERVER | cut -d: -f1)
PORT=$(echo $API_SERVER | cut -d: -f2)

# Check if nc is installed
if ! command -v nc &> /dev/null
then
    echo "nc (netcat) could not be found. Please install it first." >&2
    echo "You can install it using: sudo apt-get install netcat" >&2
    exit 1
fi

# Construct the WebSocket upgrade request
UPGRADE_REQUEST="GET /ssh-proxy?context=$CONTEXT&namespace=$NAMESPACE&pod_name=$POD_NAME HTTP/1.1\r\n"
UPGRADE_REQUEST+="Host: $API_SERVER\r\n"
UPGRADE_REQUEST+="Upgrade: websocket\r\n"
UPGRADE_REQUEST+="Connection: Upgrade\r\n"
UPGRADE_REQUEST+="Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
UPGRADE_REQUEST+="Sec-WebSocket-Version: 13\r\n"
UPGRADE_REQUEST+="\r\n"

# Send the upgrade request and then relay data
(echo -en "$UPGRADE_REQUEST"; cat) | nc $HOST $PORT
