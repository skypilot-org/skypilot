# Example RESTful Admin Policy Server

This directory contains an example implementation of a RESTful admin policy server for SkyPilot using FastAPI.

## Development

First, refer to [SkyPilot Installation](https://docs.skypilot.co/en/latest/getting-started/installation.html) to install SkyPilot.

Then start the server:

```bash
python policy_server.py --policy DoNothingPolicy
```

The server will apply the specified policy to the user request. Available policies can be found in the [example_policy module](../example_policy/example_policy).

Finally, [set the admin policy at SkyPilot config](https://docs.skypilot.co/en/latest/cloud-setup/policy.html) to use the policy server:

```yaml
admin_policy: http://localhost:8080
```
