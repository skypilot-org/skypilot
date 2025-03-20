#!/bin/bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate base

# Function to handle SIGTERM
handle_sigterm() {
    echo "Received SIGTERM, cleaning up resources..."
    bash ~/stop_sky_resource.sh
    exit 0
}

trap handle_sigterm SIGTERM

cd /skypilot
pip uninstall -y skypilot
uv pip install --prerelease=allow "azure-cli>=2.65.0"
uv pip install -r requirements-dev.txt
uv pip install -e ".[all]"
sky api start --deploy
# Keep the container running by tailing the API server log
tail -f ~/.sky/api_server/server.log
