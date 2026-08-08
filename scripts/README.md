# Optional provider tools

The production SkyPilot image includes two optional provider-integration
executables:

- /usr/local/bin/refresh-runpod-catalog.py refreshes the RunPod GPU catalog.
- /usr/local/bin/skypilot-server-entrypoint.sh prepares Vast and RunPod
  credentials, refreshes the RunPod catalog, and starts the SkyPilot API server.

These tools are not the image default entrypoint. They require an image built
with the RunPod and Vast extras, such as the default all-except-azure
production image configuration.

The server entrypoint preserves the existing integration contract. It requires
SKYPILOT_INITIAL_BASIC_AUTH, VAST_API_KEY, and RUNPOD_API_KEY, keeps provider
credential files owner-readable, and reuses a valid RunPod catalog within
RUNPOD_CATALOG_MAX_AGE_SECONDS (30 minutes by default).

Run the catalog utility directly with a persistent /root volume:

    docker run --rm \
      --env-file ./skypilot-server.env \
      --volume skypilot-server-root:/root \
      IMAGE \
      /usr/local/bin/refresh-runpod-catalog.py

Run the server entrypoint explicitly when the caller wants this startup
behavior:

    docker run --rm \
      --env-file ./skypilot-server.env \
      --volume skypilot-server-root:/root \
      --entrypoint tini \
      IMAGE \
      -- /usr/local/bin/skypilot-server-entrypoint.sh

Helm deployments keep their existing credential init containers and startup
command. A deployment that wants to use these tools can invoke them explicitly
from the packaged image.
