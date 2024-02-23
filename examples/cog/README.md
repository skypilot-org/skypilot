# Example: Cog + SkyPilot

Use SkyPilot to self-host any Cog-packaged projects.

This is the "Blur" example from https://github.com/replicate/cog-examples/blob/main/blur/README.md

Usage:
```console
sky launch -c cog ./sky.yaml

IP=$(sky status --ip cog)

curl http://$IP:5000/predictions -X POST \
  -H 'Content-Type: application/json' \
  -d '{"input": {"image": "https://blog.skypilot.co/introducing-sky-serve/images/sky-serve-thumbnail.png"}}' \
  | jq -r '.output | split(",")[1]' | base64 --decode > output.png
```
