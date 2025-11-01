//! Kubernetes cloud provider implementation

use crate::instance::{Instance, InstanceId, InstanceState};
use crate::provider::{CloudProvider, ProviderType, ProvisionRequest};
use async_trait::async_trait;
use k8s_openapi::api::core::v1::Pod;
use kube::{Api, Client};
use styx_core::{Error, Result};
use tracing::{debug, info};

/// Kubernetes cloud provider
pub struct KubernetesProvider {
    client: Client,
    namespace: String,
}

impl KubernetesProvider {
    /// Create new Kubernetes provider
    pub async fn new() -> Result<Self> {
        let client = Client::try_default()
            .await
            .map_err(|e| Error::Internal(format!("Failed to create k8s client: {}", e)))?;

        Ok(Self {
            client,
            namespace: "default".to_string(),
        })
    }

    /// Create with specific namespace
    pub async fn with_namespace(namespace: impl Into<String>) -> Result<Self> {
        let client = Client::try_default()
            .await
            .map_err(|e| Error::Internal(format!("Failed to create k8s client: {}", e)))?;

        Ok(Self {
            client,
            namespace: namespace.into(),
        })
    }

    /// Map pod phase to instance state
    fn map_pod_state(phase: Option<&str>) -> InstanceState {
        match phase {
            Some("Pending") => InstanceState::Pending,
            Some("Running") => InstanceState::Running,
            Some("Succeeded") | Some("Failed") => InstanceState::Terminated,
            _ => InstanceState::Error,
        }
    }
}

#[async_trait]
impl CloudProvider for KubernetesProvider {
    fn provider_type(&self) -> ProviderType {
        ProviderType::Kubernetes
    }

    async fn provision(&self, request: ProvisionRequest) -> Result<Instance> {
        info!("Provisioning Kubernetes pod: {}", request.name);

        // Mock pod for now
        let instance_id = InstanceId::new(format!("pod-{}", uuid::Uuid::new_v4().simple()));
        let mut instance = Instance::new(
            instance_id,
            &request.name,
            "pod", // Kubernetes doesn't have instance types
        );

        instance.region = self.namespace.clone();
        instance.state = InstanceState::Pending;
        instance.tags = request.tags;

        info!("Kubernetes pod provisioned: {}", instance.id);

        Ok(instance)
    }

    async fn list_instances(&self) -> Result<Vec<Instance>> {
        debug!("Listing Kubernetes pods in namespace: {}", self.namespace);

        let pods: Api<Pod> = Api::namespaced(self.client.clone(), &self.namespace);

        match pods.list(&Default::default()).await {
            Ok(pod_list) => {
                let instances: Vec<Instance> = pod_list
                    .items
                    .iter()
                    .filter_map(|pod| {
                        let name = pod.metadata.name.as_ref()?;
                        let uid = pod.metadata.uid.as_ref()?;
                        let phase = pod.status.as_ref()?.phase.as_deref();

                        let mut instance = Instance::new(
                            InstanceId::new(uid),
                            name,
                            "pod",
                        );

                        instance.state = Self::map_pod_state(phase);
                        instance.region = self.namespace.clone();

                        Some(instance)
                    })
                    .collect();

                Ok(instances)
            }
            Err(e) => Err(Error::Internal(format!("Failed to list pods: {}", e))),
        }
    }

    async fn get_instance(&self, id: &InstanceId) -> Result<Option<Instance>> {
        debug!("Getting Kubernetes pod: {}", id);
        Ok(None)
    }

    async fn terminate(&self, id: &InstanceId) -> Result<()> {
        info!("Terminating Kubernetes pod: {}", id);
        Ok(())
    }

    async fn start(&self, _id: &InstanceId) -> Result<()> {
        Err(Error::Internal("Kubernetes pods cannot be started".to_string()))
    }

    async fn stop(&self, _id: &InstanceId) -> Result<()> {
        Err(Error::Internal("Kubernetes pods cannot be stopped".to_string()))
    }

    async fn is_available(&self) -> bool {
        // Try to access the cluster
        self.client.apiserver_version().await.is_ok()
    }

    async fn list_instance_types(&self) -> Result<Vec<String>> {
        Ok(vec!["pod".to_string()])
    }

    async fn list_regions(&self) -> Result<Vec<String>> {
        // In K8s, "regions" are namespaces
        let namespaces: Api<k8s_openapi::api::core::v1::Namespace> = 
            Api::all(self.client.clone());

        match namespaces.list(&Default::default()).await {
            Ok(ns_list) => {
                let names: Vec<String> = ns_list
                    .items
                    .iter()
                    .filter_map(|ns| ns.metadata.name.clone())
                    .collect();
                Ok(names)
            }
            Err(_) => Ok(vec!["default".to_string()]),
        }
    }
}
