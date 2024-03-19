# Example: Cog + SkyPilot

Use SkyPilot to self-host any Cog-packaged projects.

This is the "Blur" example from https://github.com/replicate/cog-examples/blob/main/blur/README.md

## Serve using a single instance
```console
sky launch -c cog ./sky.yaml

IP=$(sky status --ip cog)

curl http://$IP:5000/predictions -X POST \
  -H 'Content-Type: application/json' \
  -d '{"input": {"image": "https://blog.skypilot.co/introducing-sky-serve/images/sky-serve-thumbnail.png"}}' \
  | jq -r '.output | split(",")[1]' | base64 --decode > output.png
```

## Scale up the deployment using SkyServe
We can use SkyServe (`sky serve`) to scale up the deployment to multiple instances, while enjoying load balancing, autoscaling, and other [SkyServe features](https://skypilot.readthedocs.io/en/latest/serving/sky-serve.html).
```console
sky serve up -n cog ./sky.yaml
```

Notice the only change is from `sky launch` to `sky serve up`. The same YAML can be used without changes.

After the service is launched, access the deployment with the following:
```console
ENDPOINT=$(sky serve status --endpoint cog)

curl -L http://$ENDPOINT/predictions -X POST \
  -H 'Content-Type: application/json' \
  -d '{"input": {"image": "https://blog.skypilot.co/introducing-sky-serve/images/sky-serve-thumbnail.png"}}' \
  | jq -r '.output | split(",")[1]' | base64 --decode > output.png
```
