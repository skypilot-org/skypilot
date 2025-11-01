//! Credential manager with caching

use crate::{CloudProvider, Credentials, Error, Result};
use crate::providers::{AwsProvider, AzureProvider, GcpProvider, CredentialProvider};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

/// Credential manager with caching
#[derive(Debug, Clone)]
pub struct CredentialManager {
    /// Cached credentials
    cache: Arc<RwLock<HashMap<CloudProvider, Credentials>>>,
    
    /// AWS provider
    aws_provider: Arc<AwsProvider>,
    
    /// GCP provider
    gcp_provider: Arc<GcpProvider>,
    
    /// Azure provider
    azure_provider: Arc<AzureProvider>,
}

impl Default for CredentialManager {
    fn default() -> Self {
        Self::new()
    }
}

impl CredentialManager {
    /// Create a new credential manager
    pub fn new() -> Self {
        Self {
            cache: Arc::new(RwLock::new(HashMap::new())),
            aws_provider: Arc::new(AwsProvider::new()),
            gcp_provider: Arc::new(GcpProvider::new()),
            azure_provider: Arc::new(AzureProvider::new()),
        }
    }

    /// Get credentials for a cloud provider
    pub async fn get_credentials(&self, provider: CloudProvider) -> Result<Credentials> {
        // Check cache first
        {
            let cache = self.cache.read().await;
            if let Some(creds) = cache.get(&provider) {
                return Ok(creds.clone());
            }
        }

        // Load credentials
        let creds = self.load_credentials(provider).await?;

        // Cache the credentials
        {
            let mut cache = self.cache.write().await;
            cache.insert(provider, creds.clone());
        }

        Ok(creds)
    }

    /// Load credentials for a provider (environment then file)
    async fn load_credentials(&self, provider: CloudProvider) -> Result<Credentials> {
        let provider_impl: &dyn CredentialProvider = match provider {
            CloudProvider::AWS => &*self.aws_provider,
            CloudProvider::GCP => &*self.gcp_provider,
            CloudProvider::Azure => &*self.azure_provider,
            CloudProvider::Kubernetes => {
                return Err(Error::credentials_not_found("Kubernetes"));
            }
        };

        // Try environment first
        if let Some(creds) = provider_impl.from_environment().await? {
            if provider_impl.validate(&creds).await? {
                return Ok(creds);
            }
        }

        // Try file
        if let Some(creds) = provider_impl.from_file().await? {
            if provider_impl.validate(&creds).await? {
                return Ok(creds);
            }
        }

        Err(Error::credentials_not_found(provider.as_str()))
    }

    /// Clear cached credentials for a provider
    pub async fn clear_cache(&self, provider: CloudProvider) {
        let mut cache = self.cache.write().await;
        cache.remove(&provider);
    }

    /// Clear all cached credentials
    pub async fn clear_all_cache(&self) {
        let mut cache = self.cache.write().await;
        cache.clear();
    }

    /// Refresh credentials for a provider
    pub async fn refresh(&self, provider: CloudProvider) -> Result<Credentials> {
        self.clear_cache(provider).await;
        self.get_credentials(provider).await
    }

    /// Check if credentials are cached
    pub async fn is_cached(&self, provider: CloudProvider) -> bool {
        let cache = self.cache.read().await;
        cache.contains_key(&provider)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[tokio::test]
    async fn test_credential_manager_cache() {
        env::set_var("AWS_ACCESS_KEY_ID", "AKIATEST");
        env::set_var("AWS_SECRET_ACCESS_KEY", "secret");

        let manager = CredentialManager::new();
        
        // First call - loads from environment
        let creds1 = manager.get_credentials(CloudProvider::AWS).await.unwrap();
        assert!(creds1.from_env);

        // Second call - should use cache
        assert!(manager.is_cached(CloudProvider::AWS).await);
        let creds2 = manager.get_credentials(CloudProvider::AWS).await.unwrap();
        assert_eq!(creds1.get("access_key_id"), creds2.get("access_key_id"));

        // Clear cache
        manager.clear_cache(CloudProvider::AWS).await;
        assert!(!manager.is_cached(CloudProvider::AWS).await);

        // Cleanup
        env::remove_var("AWS_ACCESS_KEY_ID");
        env::remove_var("AWS_SECRET_ACCESS_KEY");
    }

    #[tokio::test]
    async fn test_credential_manager_refresh() {
        env::set_var("AWS_ACCESS_KEY_ID", "AKIATEST");
        env::set_var("AWS_SECRET_ACCESS_KEY", "secret");

        let manager = CredentialManager::new();
        
        // Load initial credentials
        let _ = manager.get_credentials(CloudProvider::AWS).await.unwrap();
        assert!(manager.is_cached(CloudProvider::AWS).await);

        // Refresh
        let _ = manager.refresh(CloudProvider::AWS).await.unwrap();
        assert!(manager.is_cached(CloudProvider::AWS).await);

        // Cleanup
        env::remove_var("AWS_ACCESS_KEY_ID");
        env::remove_var("AWS_SECRET_ACCESS_KEY");
    }

    #[tokio::test]
    async fn test_credential_manager_not_found() {
        let manager = CredentialManager::new();
        
        // Try to get credentials for provider without env vars
        let result = manager.get_credentials(CloudProvider::Azure).await;
        assert!(result.is_err());
    }
}
