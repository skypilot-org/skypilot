# Optional provider tools

The production SkyPilot image includes an optional provider-integration
executable:

- /usr/local/bin/refresh-runpod-catalog.py refreshes the RunPod GPU catalog.

The API server also runs an internal `runpod-catalog-refresh-daemon` when
RunPod credentials are configured. It refreshes the shared catalog every 20
minutes by default. The interval can be changed in `~/.sky/config.yaml`:

    daemons:
      runpod-catalog-refresh-daemon:
        interval_seconds: 1200

RunPod marketplace capacity is additionally checked during resource selection,
so a catalog refresh does not need to be perfectly synchronized with a launch.

The utility is not the image default entrypoint. It requires an image built
with the RunPod extra and a persistent `/root` volume for its catalog output.

Run the catalog utility directly with a persistent /root volume:

    docker run --rm \
      --env-file ./skypilot-server.env \
      --volume skypilot-server-root:/root \
      IMAGE \
      /usr/local/bin/refresh-runpod-catalog.py
