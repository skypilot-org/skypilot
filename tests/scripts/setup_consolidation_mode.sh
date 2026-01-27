#!/bin/bash
# setup_consolidation_mode.sh
# Setup consolidation mode by updating API server config and restarting if needed
#
# Usage:
#   setup_consolidation_mode.sh [--jobs] [--serve] [--skip-if-backward-compat]
#
# Options:
#   --jobs                 Enable jobs consolidation mode
#   --serve                Enable serve consolidation mode
#   --skip-if-backward-compat  Skip if running backward_compat tests (checks env)

set -e

JOBS_CONSOLIDATION=false
SERVE_CONSOLIDATION=false
SKIP_IF_BACKWARD_COMPAT=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --jobs) JOBS_CONSOLIDATION=true ;;
        --serve) SERVE_CONSOLIDATION=true ;;
        --skip-if-backward-compat) SKIP_IF_BACKWARD_COMPAT=true ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

# Skip if in backward compat test and flag is set
if [ "$SKIP_IF_BACKWARD_COMPAT" = true ] && [ -n "$SKY_BACKWARD_COMPAT_TEST" ]; then
    echo "Skipping consolidation mode setup for backward compat test"
    exit 0
fi

# Get current config from API server
CURRENT_CONFIG=$(sky config show -o json 2>/dev/null || echo '{}')

# Build consolidation config
CONSOLIDATION_CONFIG="$CURRENT_CONFIG"
if [ "$JOBS_CONSOLIDATION" = true ]; then
    CONSOLIDATION_CONFIG=$(echo "$CONSOLIDATION_CONFIG" | jq '.jobs.controller.consolidation_mode = true')
fi
if [ "$SERVE_CONSOLIDATION" = true ]; then
    CONSOLIDATION_CONFIG=$(echo "$CONSOLIDATION_CONFIG" | jq '.serve.controller.consolidation_mode = true')
fi

# Check if consolidation mode is already enabled
CURRENT_JOBS_MODE=$(echo "$CURRENT_CONFIG" | jq -r '.jobs.controller.consolidation_mode // false' 2>/dev/null || echo 'false')
CURRENT_SERVE_MODE=$(echo "$CURRENT_CONFIG" | jq -r '.serve.controller.consolidation_mode // false' 2>/dev/null || echo 'false')

NEEDS_JOBS_MODE=false
if [ "$JOBS_CONSOLIDATION" = true ] && [ "$CURRENT_JOBS_MODE" != "true" ]; then
    NEEDS_JOBS_MODE=true
fi
NEEDS_SERVE_MODE=false
if [ "$SERVE_CONSOLIDATION" = true ] && [ "$CURRENT_SERVE_MODE" != "true" ]; then
    NEEDS_SERVE_MODE=true
fi

# Only restart if needed
if [ "$NEEDS_JOBS_MODE" = true ] || [ "$NEEDS_SERVE_MODE" = true ]; then
    echo "Updating consolidation mode: jobs=$JOBS_CONSOLIDATION, serve=$SERVE_CONSOLIDATION"

    # Write temporary config file
    TEMP_CONFIG=$(mktemp)
    echo "$CONSOLIDATION_CONFIG" > "$TEMP_CONFIG"

    # Update config using sky cli
    export SKYPILOT_CONFIG="$TEMP_CONFIG"
    sky api stop
    sky api start

    # Wait for API server to be ready
    sky api status

    rm -f "$TEMP_CONFIG"
    echo "Consolidation mode enabled and API server restarted"
else
    echo "Consolidation mode already configured"
fi
