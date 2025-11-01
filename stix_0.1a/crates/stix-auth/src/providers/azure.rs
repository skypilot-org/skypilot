//! Azure credential provider

use crate::{CloudProvider, Credentials, Result};
use super::CredentialProvider;
use async_trait::async_trait;
use std::env;

/// Azure credential provider
#[derive(Debug, Default)]
pub struct AzureProvider;

impl AzureProvider {
    /// Create new Azure provider
    pub fn new() -> Self {
        Self
    }
}

#[async_trait]
impl CredentialProvider for AzureProvider {
    async fn from_environment(&self) -> Result<Option<Credentials>> {
        // Check for Azure environment variables
        let tenant_id = env::var("AZURE_TENANT_ID").ok();
        let client_id = env::var("AZURE_CLIENT_ID").ok();
        let client_secret = env::var("AZURE_CLIENT_SECRET").ok();
        let subscription_id = env::var("AZURE_SUBSCRIPTION_ID").ok();

        if let (Some(tenant), Some(client), Some(secret)) = (tenant_id, client_id, client_secret) {
            let mut creds = Credentials::new(CloudProvider::Azure)
                .with_field("tenant_id", tenant)
                .with_field("client_id", client)
                .with_field("client_secret", secret)
                .from_environment();

            if let Some(sub) = subscription_id {
                creds = creds.with_field("subscription_id", sub);
            }

            Ok(Some(creds))
        } else {
            Ok(None)
        }
    }

    async fn from_file(&self) -> Result<Option<Credentials>> {
        // Azure typically uses environment variables or Azure CLI config
        // For now, return None (file-based detection can be added later)
        Ok(None)
    }

    async fn validate(&self, credentials: &Credentials) -> Result<bool> {
        // Basic validation: check required fields
        Ok(credentials.get("tenant_id").is_some()
            && credentials.get("client_id").is_some()
            && credentials.get("client_secret").is_some())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_azure_from_environment() {
        env::set_var("AZURE_TENANT_ID", "tenant-123");
        env::set_var("AZURE_CLIENT_ID", "client-456");
        env::set_var("AZURE_CLIENT_SECRET", "secret-789");
        env::set_var("AZURE_SUBSCRIPTION_ID", "sub-abc");

        let provider = AzureProvider::new();
        let result = provider.from_environment().await.unwrap();

        assert!(result.is_some());
        let creds = result.unwrap();
        assert_eq!(creds.provider, CloudProvider::Azure);
        assert!(creds.from_env);

        // Cleanup
        env::remove_var("AZURE_TENANT_ID");
        env::remove_var("AZURE_CLIENT_ID");
        env::remove_var("AZURE_CLIENT_SECRET");
        env::remove_var("AZURE_SUBSCRIPTION_ID");
    }

    #[tokio::test]
    async fn test_azure_validation() {
        let provider = AzureProvider::new();
        
        let creds = Credentials::new(CloudProvider::Azure)
            .with_field("tenant_id", "test")
            .with_field("client_id", "test")
            .with_field("client_secret", "test");

        assert!(provider.validate(&creds).await.unwrap());

        let incomplete = Credentials::new(CloudProvider::Azure)
            .with_field("tenant_id", "test");

        assert!(!provider.validate(&incomplete).await.unwrap());
    }
}
