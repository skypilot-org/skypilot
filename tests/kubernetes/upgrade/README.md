# SkyPilot Graceful Upgrade Test

Simple test script to verify SkyPilot API server handles requests during rolling updates.


## Prerequisites

Complete the helm installation guide in https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html#step-1-deploy-the-api-server-helm-chart

## Usage

```bash
./test-upgrade.sh <SERVER_URL> [RELEASE_NAME] [NAMESPACE]
```

## Example

```bash
./test-upgrade.sh http://your-api-server.com skypilot skypilot
```
