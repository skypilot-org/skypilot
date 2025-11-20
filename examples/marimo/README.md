# Run marimo on SkyPilot

Run a personal marimo server on a SkyPilot cluster.

![marimo Web UI](https://imgur.com/a/0z51X1Q "marimo Web UI")

## Launch with CLI

Launch a marimo cluser with the command:

```bash
sky launch -c marimo-example marimo.yaml
```

Next, run this command to get the endpoint to connect to over the browser:

```
sky status marimo-example --endpoints
```

