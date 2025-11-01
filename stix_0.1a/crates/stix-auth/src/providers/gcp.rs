//! GCP credential provider

use crate::{CloudProvider, Credentials, Result};
use super::CredentialProvider;
use async_trait::async_trait;
use std::env;
use std::path::PathBuf;
use tokio::fs;

/// GCP credential provider
#[derive(Debug, Default)]
pub struct GcpProvider;

impl GcpProvider {
    /// Create new GCP provider
    pub fn new() -> Self {
        Self
    }

    /// Get GCP application default credentials path
    fn adc_path() -> PathBuf {
        if let Ok(path) = env::var("GOOGLE_APPLICATION_CREDENTIALS") {
            PathBuf::from(path)
        } else {
            let home = env::var("HOME").unwrap_or_else(|_| "/root".to_string());
            PathBuf::from(home)
                .join(".config")
                .join("gcloud")
                .join("application_default_credentials.json")
        }
    }
}

#[async_trait]
impl CredentialProvider for GcpProvider {
    async fn from_environment(&self) -> Result<Option<Credentials>> {
        // Check for GOOGLE_APPLICATION_CREDENTIALS env var
        if let Ok(cred_path) = env::var("GOOGLE_APPLICATION_CREDENTIALS") {
            let mut creds = Credentials::new(CloudProvider::GCP)
                .with_field("credentials_file", cred_path)
                .from_environment();

            if let Ok(project) = env::var("GCP_PROJECT") {
                creds = creds.with_field("project_id", project);
            }

            Ok(Some(creds))
        } else {
            Ok(None)
        }
    }

    async fn from_file(&self) -> Result<Option<Credentials>> {
        let path = Self::adc_path();
        
        if !path.exists() {
            return Ok(None);
        }

        // Read credentials file (JSON format)
        let content = fs::read_to_string(&path).await?;
        
        // Parse JSON to extract fields
        let json: serde_json::Value = serde_json::from_str(&content)?;
        
        let mut creds = Credentials::new(CloudProvider::GCP)
            .with_field("credentials_file", path.to_string_lossy().to_string())
            .from_file_source();

        // Extract common fields
        if let Some(project_id) = json.get("project_id").and_then(|v| v.as_str()) {
            creds = creds.with_field("project_id", project_id);
        }

        if let Some(client_email) = json.get("client_email").and_then(|v| v.as_str()) {
            creds = creds.with_field("client_email", client_email);
        }

        if let Some(cred_type) = json.get("type").and_then(|v| v.as_str()) {
            creds = creds.with_field("type", cred_type);
        }

        Ok(Some(creds))
    }

    async fn validate(&self, credentials: &Credentials) -> Result<bool> {
        // Basic validation: check that credentials_file exists
        Ok(credentials.get("credentials_file").is_some())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_gcp_from_environment() {
        env::set_var("GOOGLE_APPLICATION_CREDENTIALS", "/path/to/creds.json");
        env::set_var("GCP_PROJECT", "my-project");

        let provider = GcpProvider::new();
        let result = provider.from_environment().await.unwrap();

        assert!(result.is_some());
        let creds = result.unwrap();
        assert_eq!(creds.provider, CloudProvider::GCP);
        assert!(creds.from_env);

        // Cleanup
        env::remove_var("GOOGLE_APPLICATION_CREDENTIALS");
        env::remove_var("GCP_PROJECT");
    }

    #[tokio::test]
    async fn test_gcp_validation() {
        let provider = GcpProvider::new();
        
        let creds = Credentials::new(CloudProvider::GCP)
            .with_field("credentials_file", "/path/to/file");

        assert!(provider.validate(&creds).await.unwrap());

        let incomplete = Credentials::new(CloudProvider::GCP);
        assert!(!provider.validate(&incomplete).await.unwrap());
    }
}
