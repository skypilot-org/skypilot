#!/bin/bash

# Launch 20 Sky jobs in parallel
for i in {1..20}; do
    sky jobs launch --async -y shutdown.yaml &
done

# Wait for all background processes to complete
wait
