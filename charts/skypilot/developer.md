# Developer Guide

## SkyPilot Config Persistency

**Design Decision:** Use PVC as the source of truth for SkyPilot configuration.

**Drawback:** Helm upgrades cannot directly change the SkyPilot config for 
the API server. Instead, we provide instructions for updating config 
(see: https://docs.skypilot.co/en/latest/reference/api-server/api-server-admin-deploy.html#setting-the-skypilot-config).

### Why PVC?
- **Fast:** Immediate reflection of config changes
- **Unified:** Consistent with non-Kubernetes deployments  
- **Future-proof:** Enables migration to external storage sources

### Why Not ConfigMap?
- **Slow updates:** Changes take 2-3 seconds to reflect in mounted files, 
  making SkyPilot unresponsive (known Kubernetes issue with no planned fix: 
  [kubernetes/kubernetes#50345](https://github.com/kubernetes/kubernetes/issues/50345#issuecomment-585344794))
- **Poor persistence:** Config is not persisted across Kubernetes clusters 
  and backing up is difficult

**Note:** SkyPilot syncs config back to ConfigMap for user convenience, but 
ConfigMap may not always be in sync with PVC (e.g., user `helm upgrade` with a
new configMap). Sync occurs when config changes are made through the workspace
API.

**TODO:** Provide API to get config directly from API server to eliminate 
ConfigMap dependency.
