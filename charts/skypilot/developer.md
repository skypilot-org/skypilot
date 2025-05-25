# Developer Guide

## SkyPilot config persistency

Helm deployed API server has some limitation for config persistency. It is intentionally decided to use PVC/external storage for config persistency, instead
of configMap, as configMap is not fully persisted across Kuberenetes cluster.

SkyPilot will still sync the config back to configMap for user convenience, but
there are some scenarios where the configMap is not fully in sync with the PVC:

* When the API server is upgraded and the configMap is ignored (no `forceConfigOverride` set), it will only be synced back to configMap when any
config change is made through workspace API.

TODO: SkyPilot should provide API to get config from API server, to avoid the dependency on configMap.
