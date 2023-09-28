#!/bin/bash

average=0
# Check if the command line argument (suffix) is provided
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <exp_suffix>"
    exit 1
fi

suffix="$1"

# Declare a file to store the output of the time command with the given suffix
output_file="skylaunch_results_${suffix}.txt"

runexpt=1

# Check if the output file exists and ask if it should be overwritten
if [ -f "$output_file" ]; then
    read -p "The output file $output_file already exists. Do you want to overwrite it? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      echo "Analyzing existing results..."
      runexpt=0
    fi
fi

if [ "$runexpt" -eq 1 ]; then
    # Delete existing results
    rm -f "$output_file"
    for i in {1..5}; do
        (
            # Use the `time` command in a subshell to capture its output
            time sky launch -y -c test
        ) 2>> "$output_file"
        sky down -y test
    done
fi

# Process the results from the 2nd to 5th run
count=0
while read -r line; do
    # Check for the real time output from the time command
    if [[ $line == real* ]]; then
        if [ "$count" -eq 0 ]; then
            # Skip first result
            count=$((count+1))
            continue
        fi
        count=$((count+1))
        # Extract the minutes and seconds and convert to seconds
        minutes=$(echo $line | cut -d'm' -f1 | sed 's/real //')
        seconds=$(echo $line | cut -d'm' -f2 | cut -d's' -f1)
        total_seconds=$(echo "$minutes*60 + $seconds" | bc)
        # Accumulate the total time
        average=$(echo "$average + $total_seconds" | bc)
    fi
done < <(cat "$output_file")  # start reading from the 2nd run

# Subtract one from the count to account for the skipped first result
count=$((count-1))
# Compute the average time
average=$(echo "scale=2; $average/$count" | bc)

# Print the average time
echo "Average total time (from 2nd to 5th run): $average seconds"
