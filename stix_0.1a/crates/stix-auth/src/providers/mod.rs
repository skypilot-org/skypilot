//! Cloud provider credential detection

pub mod aws;
pub mod gcp;
pub mod azure;

pub use aws::AwsProvider;
pub use gcp::GcpProvider;
pub use azure::AzureProvider;

use crate::{Credentials, Result};
use async_trait::async_trait;

/// Trait for credential providers
#[async_trait]
pub trait CredentialProvider: Send + Sync {
    /// Detect credentials from environment
    async fn from_environment(&self) -> Result<Option<Credentials>>;
    
    /// Detect credentials from file
    async fn from_file(&self) -> Result<Option<Credentials>>;
    
    /// Validate credentials
    async fn validate(&self, credentials: &Credentials) -> Result<bool>;
}
