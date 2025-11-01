//! Credential structures

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Cloud provider identifier
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum CloudProvider {
    /// Amazon Web Services
    AWS,
    /// Google Cloud Platform
    GCP,
    /// Microsoft Azure
    Azure,
    /// Kubernetes
    Kubernetes,
}

impl CloudProvider {
    /// Get provider name as string
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::AWS => "aws",
            Self::GCP => "gcp",
            Self::Azure => "azure",
            Self::Kubernetes => "kubernetes",
        }
    }
}

impl std::fmt::Display for CloudProvider {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

/// Credentials for cloud providers
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Credentials {
    /// Cloud provider
    pub provider: CloudProvider,
    
    /// Credential fields (key-value pairs)
    pub fields: HashMap<String, String>,
    
    /// Whether credentials were loaded from environment
    pub from_env: bool,
    
    /// Whether credentials were loaded from file
    pub from_file: bool,
}

impl Credentials {
    /// Create new credentials
    pub fn new(provider: CloudProvider) -> Self {
        Self {
            provider,
            fields: HashMap::new(),
            from_env: false,
            from_file: false,
        }
    }

    /// Add a field
    pub fn with_field(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.fields.insert(key.into(), value.into());
        self
    }

    /// Mark as loaded from environment
    pub fn from_environment(mut self) -> Self {
        self.from_env = true;
        self
    }

    /// Mark as loaded from file
    pub fn from_file_source(mut self) -> Self {
        self.from_file = true;
        self
    }

    /// Get a field value
    pub fn get(&self, key: &str) -> Option<&String> {
        self.fields.get(key)
    }

    /// Check if credentials are valid (non-empty)
    pub fn is_valid(&self) -> bool {
        !self.fields.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cloud_provider_display() {
        assert_eq!(CloudProvider::AWS.to_string(), "aws");
        assert_eq!(CloudProvider::GCP.to_string(), "gcp");
        assert_eq!(CloudProvider::Azure.to_string(), "azure");
    }

    #[test]
    fn test_credentials_builder() {
        let creds = Credentials::new(CloudProvider::AWS)
            .with_field("access_key", "AKIATEST")
            .with_field("secret_key", "secret")
            .from_environment();

        assert_eq!(creds.provider, CloudProvider::AWS);
        assert_eq!(creds.get("access_key"), Some(&"AKIATEST".to_string()));
        assert!(creds.from_env);
        assert!(creds.is_valid());
    }

    #[test]
    fn test_empty_credentials() {
        let creds = Credentials::new(CloudProvider::GCP);
        assert!(!creds.is_valid());
    }
}
