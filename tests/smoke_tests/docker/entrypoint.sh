#!/bin/bash
source $HOME/miniconda3/etc/profile.d/conda.sh
conda activate base
# Add uv to PATH
export PATH="$PATH:$HOME/.local/bin"
USER_HASH_FILE="$HOME/.sky/user_hash"

# Function to handle SIGTERM
handle_sigterm() {
    echo "Received SIGTERM, cleaning up resources..."
    kill -TERM $TAIL_PID
    wait $TAIL_PID
    bash $HOME/stop_sky_resource.sh
    exit 0
}

start_sky_api_server() {
    # We only bind the 0.0.0.0 host to expose to the host machine after setup is complete
    sky api start --deploy
    trap handle_sigterm SIGTERM
    tail -f $HOME/.sky/api_server/server.log &
    TAIL_PID=$!
    echo "Tail PID: $TAIL_PID"
    wait $TAIL_PID
}

# Function to regenerate user hash
regenerate_user_hash() {
    local default_hash_length=8
    local old_hash=""

    # Check if the file exists
    if [[ -f "$USER_HASH_FILE" ]]; then
        # Read the current content of the file
        old_hash=$(cat "$USER_HASH_FILE")

        # Get the length of the current hash
        hash_length=${#old_hash}
    else
        # If the file does not exist, use the default hash length
        hash_length=$default_hash_length
    fi

    # Generate a new random UUID and cut it to the desired length
    local new_hash=$(uuidgen | tr -d '-' | head -c "$hash_length")

    # Overwrite the file with the new hash
    echo "$new_hash" > "$USER_HASH_FILE"

    # Notify the user of the change
    if [[ -n "$old_hash" ]]; then
        echo "Updated $USER_HASH_FILE with new hash: $new_hash, old hash: $old_hash"
    else
        echo "Created $USER_HASH_FILE with new hash: $new_hash"
    fi

    return 0
}

# if TEMP_FILE_FOR_TEST exists, skip the following steps
if [ -f "$HOME/TEMP_FILE_FOR_TEST" ]; then
    echo "TEMP_FILE_FOR_TEST exists, skipping the following steps"
    start_sky_api_server
    exit 0
fi

# Check if LAUNCHED_BY_DOCKER_CONTAINER environment variable is set
if [ -n "$LAUNCHED_BY_DOCKER_CONTAINER" ]; then
    echo "Container launched by Docker, waiting for /success_mount_directory to be available..."

    # Set timeout for 5 minutes (300 seconds)
    timeout=300
    start_time=$(date +%s)

    # Loop until /skypilot exists or timeout is reached
    while [ ! -d "/success_mount_directory" ]; do
        current_time=$(date +%s)
        elapsed=$((current_time - start_time))

        if [ $elapsed -ge $timeout ]; then
            echo "Timeout reached waiting for /success_mount_directory directory"
            break
        fi

        echo "Waiting for /success_mount_directory... ($elapsed seconds elapsed)"
        sleep 5
    done
fi

# Check if /skypilot exists
if [ ! -d "/skypilot" ]; then
    echo "ERROR: /skypilot directory does not exist"
    exit 1
fi

cd /skypilot
uv pip uninstall skypilot
uv pip install --prerelease=allow "azure-cli>=2.65.0"
uv pip install -r requirements-dev.txt
uv pip install -e ".[all]"
sky api start
sky check
sky api stop

# Execute the hash regeneration function
regenerate_user_hash

# Create a temporary file to initialize only once
touch $HOME/TEMP_FILE_FOR_TEST

start_sky_api_server
