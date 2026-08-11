# Optional provider tools

The production SkyPilot image includes an optional provider-integration
executable:

- /usr/local/bin/refresh-runpod-catalog.py refreshes the RunPod GPU catalog.

The utility is not the image default entrypoint. It requires an image built
with the RunPod extra and a persistent `/root` volume for its catalog output.

Run the catalog utility directly with a persistent /root volume:

    docker run --rm \
      --env-file ./skypilot-server.env \
      --volume skypilot-server-root:/root \
      IMAGE \
      /usr/local/bin/refresh-runpod-catalog.py
