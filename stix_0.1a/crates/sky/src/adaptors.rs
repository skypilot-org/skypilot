//! Cloud adaptors
//!
//! Maps high-level `Resources` selections to concrete cloud providers and
//! backends. The goal is to keep the rest of the crate free from conditional
//! logic about individual providers.

use crate::backends::{Backend, CloudVmRayBackend, LocalBackend};
use crate::clouds::{self, CloudProvider};
use crate::exceptions::{Result, SkyError};
use crate::resources::Resources;

/// Backend resolution outcome.
pub struct BackendContext {
    /// Backend capable of provisioning, executing and managing workloads.
    pub backend: Box<dyn Backend>,
    /// Effective cloud identifier (lowercase).
    pub cloud: String,
}

/// Resolve a backend for the requested `Resources`.
///
/// `fallback_cloud` is used when no cloud is set in the resources (for example,
/// when operating on an existing cluster where only the handle is available).
pub fn backend_for(
    resources: Option<&Resources>,
    fallback_cloud: Option<&str>,
) -> Result<BackendContext> {
    let cloud_name = resolve_cloud(resources, fallback_cloud)?;
    let backend: Box<dyn Backend> = match cloud_name.as_str() {
        "local" => Box::new(LocalBackend),
        "aws" => Box::new(CloudVmRayBackend::new(provider_for_aws(resources))),
        "gcp" => Box::new(CloudVmRayBackend::new(provider_for_gcp(resources))),
        "azure" => Box::new(CloudVmRayBackend::new(provider_for_azure())),
        "kubernetes" | "k8s" => Box::new(CloudVmRayBackend::new(provider_for_k8s(resources))),
        other => {
            return Err(SkyError::ConfigurationError(format!(
                "Unsupported cloud provider '{}'",
                other
            )))
        }
    };

    Ok(BackendContext {
        backend,
        cloud: cloud_name,
    })
}

fn resolve_cloud(resources: Option<&Resources>, fallback_cloud: Option<&str>) -> Result<String> {
    if let Some(resources) = resources {
        if let Some(cloud) = resources.cloud() {
            return Ok(normalize_cloud(cloud));
        }
    }

    if let Some(cloud) = fallback_cloud {
        return Ok(normalize_cloud(cloud));
    }

    Ok("local".to_string())
}

fn normalize_cloud(cloud: &str) -> String {
    match cloud.trim().to_lowercase().as_str() {
        "aws" => "aws",
        "gcp" => "gcp",
        "azure" => "azure",
        "kubernetes" | "k8s" => "kubernetes",
        "local" | "localhost" => "local",
        other => other,
    }
    .to_string()
}

fn provider_for_aws(resources: Option<&Resources>) -> Box<dyn CloudProvider> {
    let mut provider = clouds::aws::AWS::new();

    if let Some(resources) = resources {
        if let Some(region) = resources.region() {
            provider = clouds::aws::AWS::with_region(region);
        }
    }

    Box::new(provider)
}

fn provider_for_gcp(resources: Option<&Resources>) -> Box<dyn CloudProvider> {
    let mut provider = clouds::gcp::GCP::new();

    if let Some(resources) = resources {
        if let Some(region) = resources.region() {
            provider = provider.with_region(region);
        }
    }

    Box::new(provider)
}

fn provider_for_azure() -> Box<dyn CloudProvider> {
    Box::new(clouds::azure::Azure::new())
}

fn provider_for_k8s(resources: Option<&Resources>) -> Box<dyn CloudProvider> {
    let mut provider = clouds::kubernetes::Kubernetes::new();

    if let Some(resources) = resources {
        if let Some(context) = resources.region() {
            provider = provider.with_context(context);
        }
        if let Some(namespace) = resources.zone() {
            provider = provider.with_namespace(namespace);
        }
    }

    Box::new(provider)
}
