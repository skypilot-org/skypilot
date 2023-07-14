# Sky Serve

Serving library for SkyPilot.

The goal of Sky Serve is simple - expose one endpoint, that redirects to serving endpoints running on different resources, regions and clouds.

Sky Serve transparently handles load balancing, failover and autoscaling of the serving endpoints.

## Architecture

Sky Serve has four key components:
1. Redirector - The HTTP server is responsible for recieving requests and redirecting them to healthy endpoints.
2. Load balancers - spread requests across healthy endpoints according to different policies.
3. Autoscalers - scale up and down the number of serving endpoints according to different policies and handle recovery of unhealthy endpoints.
4. Infra Providers - provides a uniform interface to talk to SkyPilot.

## Usage
** Work in progress**
```bash
# Run controller.
python -m sky.serve.controller --task-yaml examples/fastchat/api_server.yaml

# Run redirector.
python -m sky.serve.redirector
```