#!/bin/bash

# Check if the correct number of arguments are passed
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 IP:PORT QUERY_RATE(QPS)"
    exit 1
fi

ENDPOINT=$1
RATE=$2
# Calculate the delay between requests in seconds.
DELAY=$(awk "BEGIN {print 1/$RATE}")

# Function to send a single query
send_query() {
  echo "Sending a query to $ENDPOINT"
  curl -L http://$ENDPOINT/v1/chat/completions \
       -H "Content-Type: application/json" \
       -d '{
             "model": "llama3",
             "messages": [
               {
                 "role": "system",
                 "content": "You are a helpful assistant."
               },
               {
                 "role": "user",
                 "content": "Who are you?"
               }
             ]
           }'
}

export -f send_query
export ENDPOINT

# Generate and send queries in parallel
if (( $(echo "$RATE >= 1" | bc -l) )); then
    # For rates equal to or greater than 1 query per second
    while true; do
        for ((i=0; i<$RATE; i++)); do
            send_query &
        done
        sleep 1
    done
else
    # For fractional rates (less than 1 query per second)
    while true; do
        send_query &
        sleep $DELAY
    done
fi
