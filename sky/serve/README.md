# Sky Serve

Serving library for SkyPilot.

The goal of Sky Serve is simple - expose one endpoint, that redirects to serving endpoints running on different resources, regions and clouds.

Sky Serve transparently handles load balancing, failover and autoscaling of the serving endpoints.

## Architecture

![Architecture](../../docs/source/images/sky-serve-architecture.png)

Sky Serve has four key components:
1. Redirector - receiving requests and redirecting them to healthy endpoints.
2. Load balancers - spread requests across healthy endpoints according to different policies.
3. Autoscalers - scale up and down the number of serving endpoints according to different policies.
4. Replica Managers -  monitoring replica status and handle recovery of unhealthy endpoints.

## Usage

[User doc](https://docs.google.com/document/d/1vVmzLF-EkG3Moj-q47DQBGvFipK4PNfkz0V6LyaPstE/edit)
