//! AWS credential provider

use crate::{CloudProvider, Credentials, Error, Result};
use super::CredentialProvider;
use async_trait::async_trait;
use std::env;
use std::path::PathBuf;
use tokio::fs;

/// AWS credential provider
#[derive(Debug, Default)]
pub struct AwsProvider;

impl AwsProvider {
    /// Create new AWS provider
    pub fn new() -> Self {
        Self
    }

    /// Get AWS credentials file path
    fn credentials_path() -> PathBuf {
        if let Ok(path) = env::var("AWS_SHARED_CREDENTIALS_FILE") {
            PathBuf::from(path)
        } else {
            let home = env::var("HOME").unwrap_or_else(|_| "/root".to_string());
            PathBuf::from(home).join(".aws").join("credentials")
        }
    }
}

#[async_trait]
impl CredentialProvider for AwsProvider {
    async fn from_environment(&self) -> Result<Option<Credentials>> {
        // Check for AWS environment variables
        let access_key = env::var("AWS_ACCESS_KEY_ID").ok();
        let secret_key = env::var("AWS_SECRET_ACCESS_KEY").ok();
        let session_token = env::var("AWS_SESSION_TOKEN").ok();
        let region = env::var("AWS_DEFAULT_REGION")
            .or_else(|_| env::var("AWS_REGION"))
            .ok();

        if let (Some(access), Some(secret)) = (access_key, secret_key) {
            let mut creds = Credentials::new(CloudProvider::AWS)
                .with_field("access_key_id", access)
                .with_field("secret_access_key", secret)
                .from_environment();

            if let Some(token) = session_token {
                creds = creds.with_field("session_token", token);
            }

            if let Some(r) = region {
                creds = creds.with_field("region", r);
            }

            Ok(Some(creds))
        } else {
            Ok(None)
        }
    }

    async fn from_file(&self) -> Result<Option<Credentials>> {
        let path = Self::credentials_path();
        
        if !path.exists() {
            return Ok(None);
        }

        // Read credentials file
        let content = fs::read_to_string(&path).await?;
        
        // Parse simple INI-style format for [default] profile
        let mut access_key = None;
        let mut secret_key = None;
        let mut region = None;
        let mut in_default = false;

        for line in content.lines() {
            let line = line.trim();
            
            if line.starts_with('[') {
                in_default = line == "[default]";
                continue;
            }

            if in_default && line.contains('=') {
                let parts: Vec<&str> = line.splitn(2, '=').collect();
                if parts.len() == 2 {
                    let key = parts[0].trim();
                    let value = parts[1].trim();
                    
                    match key {
                        "aws_access_key_id" => access_key = Some(value.to_string()),
                        "aws_secret_access_key" => secret_key = Some(value.to_string()),
                        "region" => region = Some(value.to_string()),
                        _ => {}
                    }
                }
            }
        }

        if let (Some(access), Some(secret)) = (access_key, secret_key) {
            let mut creds = Credentials::new(CloudProvider::AWS)
                .with_field("access_key_id", access)
                .with_field("secret_access_key", secret)
                .from_file_source();

            if let Some(r) = region {
                creds = creds.with_field("region", r);
            }

            Ok(Some(creds))
        } else {
            Ok(None)
        }
    }

    async fn validate(&self, credentials: &Credentials) -> Result<bool> {
        // Basic validation: check required fields exist
        Ok(credentials.get("access_key_id").is_some() 
            && credentials.get("secret_access_key").is_some())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_aws_from_environment() {
        env::set_var("AWS_ACCESS_KEY_ID", "AKIATEST");
        env::set_var("AWS_SECRET_ACCESS_KEY", "secret");
        env::set_var("AWS_DEFAULT_REGION", "us-east-1");

        let provider = AwsProvider::new();
        let result = provider.from_environment().await.unwrap();

        assert!(result.is_some());
        let creds = result.unwrap();
        assert_eq!(creds.provider, CloudProvider::AWS);
        assert_eq!(creds.get("access_key_id"), Some(&"AKIATEST".to_string()));
        assert!(creds.from_env);

        // Cleanup
        env::remove_var("AWS_ACCESS_KEY_ID");
        env::remove_var("AWS_SECRET_ACCESS_KEY");
        env::remove_var("AWS_DEFAULT_REGION");
    }

    #[tokio::test]
    async fn test_aws_validation() {
        let provider = AwsProvider::new();
        
        let creds = Credentials::new(CloudProvider::AWS)
            .with_field("access_key_id", "test")
            .with_field("secret_access_key", "test");

        assert!(provider.validate(&creds).await.unwrap());

        let incomplete = Credentials::new(CloudProvider::AWS)
            .with_field("access_key_id", "test");

        assert!(!provider.validate(&incomplete).await.unwrap());
    }
}
