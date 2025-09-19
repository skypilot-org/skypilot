#!/bin/bash

# Check if 'sky' command exists
if ! command -v sky &> /dev/null; then
    echo "Error: 'sky' command not found. Please ensure SkyPilot CLI is installed and available in the PATH."
    exit 1
fi

# Execute SkyPilot cleanup commands
execute_sky_cleanup() {
    sky down -a -y -u
    sky storage delete -a -y
    sky jobs cancel -a -y -u
    sky serve down -a -y
    sky jobs pool down -a -y
}

execute_sky_cleanup

# Function to handle specific controllers in the sky status output
handle_sky_controllers() {
    # Execute sky cleanup commands before handling controllers
    execute_sky_cleanup
    echo "Checking for specific controllers in the sky status output..."
    sky status --refresh -u | while read -r line; do
        if echo "$line" | grep -qE 'sky-(serve|jobs)-controller-[a-zA-Z0-9]+'; then
            NAME=$(echo "$line" | awk '{print $1}' | sed 's/[:.]$//')
            echo "Found controller: $NAME. Executing sky down $NAME."
            # Run `sky down "$NAME"` with a timeout to avoid it hanging
            output=$(timeout 10 sky down "$NAME" 2>&1)
            # Use `expect` to handle interactive input
            # Use `expect` to handle the `sky down` command
            # Check if the command output indicates a "delete" prompt
            if echo "$output" | grep -q "To proceed, please type 'delete':"; then
                echo "Detected 'delete' prompt. Re-running with expect to handle it."
                expect <<EOF
spawn sky down "$NAME"
expect "To proceed, please type 'delete':"
send "delete\r"

# Set a custom timeout of 100 seconds
set timeout 100
expect eof
EOF
                return  # Exit after handling the "delete" case
            fi

            output=$(echo "$output" | sed -r "s/\x1B\[[0-9;]*[mK]//g")
            echo "$output" > debug_clean_output.txt
            output=$(cat debug_clean_output.txt)
            # Check if the command output contains the "fix it with" message
            if echo "$output" | grep -q "Please wait until the sky serve controller is UP or fix it with"; then
                echo "$output"
                # Extract the suggested `sky start` command
                #start_command=$(echo "$clean_output" | grep -o "fix it with sky start [^\.]*" | sed 's/fix it with //')
                start_command="sky start $NAME"
                echo "Detected 'fix it with' message. Running: $start_command"
                start_output=$($start_command 2>&1)
                echo "Command output: $start_output"

                return  # Exit after handling the "fix it with" case
            fi

            if echo "$output" | grep -q "Please terminate the services first with"; then
                echo "$output"
                stop_command="sky serve down -a -y"
                echo "Please terminate the services. Running: $stop_command"
                stop_coutput=$($stop_command 2>&1)
                echo "Command output: $stop_coutput"

                # Extract all `sky serve down ... --purge` commands
                stop_coutput=$(echo "$stop_coutput" | sed -r "s/\x1B\[[0-9;]*[mK]//g")
                purge_commands=$(echo "$stop_coutput" | grep -oP "sky serve down .*? --purge")

                # Execute each extracted command
                while IFS= read -r cmd; do
                    echo "Executing: $cmd"
                    cmd_output=$($cmd 2>&1)
                    echo "Command output: $cmd_output"
                done <<< "$purge_commands"
            fi
            # Log unexpected output
            echo "Unexpected output from 'sky down $NAME':"
            echo "$output"
        fi
    done
}

# kind delete cluster --name kind-cluster-2

# Define the contents to match
MATCH_CONTENT_1="No existing clusters."
MATCH_CONTENT_2="No in-progress managed jobs."
MATCH_CONTENT_3="No live services."

# Define the total duration and interval
TOTAL_DURATION=600  # 10 minutes in seconds
INTERVAL=30         # 30 seconds

# Calculate the number of attempts
ATTEMPTS=$((TOTAL_DURATION / INTERVAL))

# Function to check the status
check_status() {
    sky status --refresh -u
}

# Loop to check the status every 30 seconds
for ((i=0; i<ATTEMPTS; i++)); do
    STATUS_OUTPUT=$(check_status)
    if echo "$STATUS_OUTPUT" | grep -q "$MATCH_CONTENT_1" && \
       echo "$STATUS_OUTPUT" | grep -q "$MATCH_CONTENT_2" && \
       echo "$STATUS_OUTPUT" | grep -q "$MATCH_CONTENT_3"; then
        echo "Success: All content matched."
        exit 0
    else
        echo "Attempt $((i+1)): Content not matched. Current status:"
        echo "$STATUS_OUTPUT"
        handle_sky_controllers
        echo "Sleeping for $INTERVAL seconds before retrying..."
    fi
    sleep $INTERVAL
done

echo "Failure: Content not matched within 10 minutes."
exit 1
