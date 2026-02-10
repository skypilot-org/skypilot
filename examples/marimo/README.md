# Run marimo on SkyPilot

Run a personal [marimo](https://marimo.io/) server on a SkyPilot cluster.

![marimo Web UI](https://i.imgur.com/iLYbQ6b.png "marimo Web UI")

## Launch with CLI

Launch a marimo cluster with the command:

```bash
sky launch -c marimo-example marimo.yaml
```

Next, run this command to get the endpoint to connect via the browser:

```
sky status marimo-example --endpoints
```

## Customization

The `marimo.yaml` file can be customized to change the port, password, and other options. Check the [docs](https://docs.marimo.io/cli/#marimo-edit) for more information.
