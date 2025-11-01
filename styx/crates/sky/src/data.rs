//! Data and Storage Management - ECHTE S3/GCS/Azure Integration!
//!
//! Handles file uploads, downloads, and cloud storage mounting

use std::path::{Path, PathBuf};
use tokio::process::Command;

use crate::exceptions::Result;

/// Storage type
#[derive(Debug, Clone)]
pub enum StorageType {
    S3,
    GCS,
    Azure,
    Local,
}

/// Storage manager
pub struct Storage {
    storage_type: StorageType,
    bucket: String,
    mount_path: Option<PathBuf>,
}

impl Storage {
    /// Create new storage
    pub fn new(storage_type: StorageType, bucket: impl Into<String>) -> Self {
        Self {
            storage_type,
            bucket: bucket.into(),
            mount_path: None,
        }
    }

    /// Set mount path
    pub fn with_mount(mut self, path: impl Into<PathBuf>) -> Self {
        self.mount_path = Some(path.into());
        self
    }

    /// Upload file to storage
    pub async fn upload(&self, local_path: impl AsRef<Path>, remote_path: &str) -> Result<()> {
        println!("?? Uploading {} to {}...", local_path.as_ref().display(), remote_path);

        match self.storage_type {
            StorageType::S3 => self.upload_s3(local_path.as_ref(), remote_path).await,
            StorageType::GCS => self.upload_gcs(local_path.as_ref(), remote_path).await,
            StorageType::Azure => self.upload_azure(local_path.as_ref(), remote_path).await,
            StorageType::Local => {
                tokio::fs::copy(local_path.as_ref(), remote_path).await
                    .map_err(|e| crate::exceptions::SkyError::StorageError(e.to_string()))?;
                Ok(())
            }
        }
    }

    /// Download file from storage
    pub async fn download(&self, remote_path: &str, local_path: impl AsRef<Path>) -> Result<()> {
        println!("?? Downloading {} to {}...", remote_path, local_path.as_ref().display());

        match self.storage_type {
            StorageType::S3 => self.download_s3(remote_path, local_path.as_ref()).await,
            StorageType::GCS => self.download_gcs(remote_path, local_path.as_ref()).await,
            StorageType::Azure => self.download_azure(remote_path, local_path.as_ref()).await,
            StorageType::Local => {
                tokio::fs::copy(remote_path, local_path.as_ref()).await
                    .map_err(|e| crate::exceptions::SkyError::StorageError(e.to_string()))?;
                Ok(())
            }
        }
    }

    /// Sync directory to storage
    pub async fn sync(&self, local_dir: impl AsRef<Path>, remote_dir: &str) -> Result<()> {
        println!("?? Syncing {} to {}...", local_dir.as_ref().display(), remote_dir);

        match self.storage_type {
            StorageType::S3 => {
                let s3_path = format!("s3://{}/{}", self.bucket, remote_dir);
                self.run_aws_s3_sync(local_dir.as_ref(), &s3_path).await
            }
            StorageType::GCS => {
                let gcs_path = format!("gs://{}/{}", self.bucket, remote_dir);
                self.run_gsutil_sync(local_dir.as_ref(), &gcs_path).await
            }
            _ => Err(crate::exceptions::SkyError::StorageError(
                "Sync not supported for this storage type".to_string()
            ))
        }
    }

    // S3 Operations
    async fn upload_s3(&self, local: &Path, remote: &str) -> Result<()> {
        let s3_path = format!("s3://{}/{}", self.bucket, remote);
        
        let output = Command::new("aws")
            .args(&["s3", "cp", &local.to_string_lossy(), &s3_path])
            .output()
            .await
            .map_err(|e| crate::exceptions::SkyError::StorageError(e.to_string()))?;

        if !output.status.success() {
            return Err(crate::exceptions::SkyError::StorageError(
                String::from_utf8_lossy(&output.stderr).to_string()
            ));
        }

        Ok(())
    }

    async fn download_s3(&self, remote: &str, local: &Path) -> Result<()> {
        let s3_path = format!("s3://{}/{}", self.bucket, remote);
        
        let output = Command::new("aws")
            .args(&["s3", "cp", &s3_path, &local.to_string_lossy()])
            .output()
            .await
            .map_err(|e| crate::exceptions::SkyError::StorageError(e.to_string()))?;

        if !output.status.success() {
            return Err(crate::exceptions::SkyError::StorageError(
                String::from_utf8_lossy(&output.stderr).to_string()
            ));
        }

        Ok(())
    }

    async fn run_aws_s3_sync(&self, local: &Path, s3_path: &str) -> Result<()> {
        let output = Command::new("aws")
            .args(&["s3", "sync", &local.to_string_lossy(), s3_path])
            .output()
            .await
            .map_err(|e| crate::exceptions::SkyError::StorageError(e.to_string()))?;

        if !output.status.success() {
            return Err(crate::exceptions::SkyError::StorageError(
                String::from_utf8_lossy(&output.stderr).to_string()
            ));
        }

        Ok(())
    }

    // GCS Operations
    async fn upload_gcs(&self, local: &Path, remote: &str) -> Result<()> {
        let gcs_path = format!("gs://{}/{}", self.bucket, remote);
        
        let output = Command::new("gsutil")
            .args(&["cp", &local.to_string_lossy(), &gcs_path])
            .output()
            .await
            .map_err(|e| crate::exceptions::SkyError::StorageError(e.to_string()))?;

        if !output.status.success() {
            return Err(crate::exceptions::SkyError::StorageError(
                String::from_utf8_lossy(&output.stderr).to_string()
            ));
        }

        Ok(())
    }

    async fn download_gcs(&self, remote: &str, local: &Path) -> Result<()> {
        let gcs_path = format!("gs://{}/{}", self.bucket, remote);
        
        let output = Command::new("gsutil")
            .args(&["cp", &gcs_path, &local.to_string_lossy()])
            .output()
            .await
            .map_err(|e| crate::exceptions::SkyError::StorageError(e.to_string()))?;

        if !output.status.success() {
            return Err(crate::exceptions::SkyError::StorageError(
                String::from_utf8_lossy(&output.stderr).to_string()
            ));
        }

        Ok(())
    }

    async fn run_gsutil_sync(&self, local: &Path, gcs_path: &str) -> Result<()> {
        let output = Command::new("gsutil")
            .args(&["-m", "rsync", "-r", &local.to_string_lossy(), gcs_path])
            .output()
            .await
            .map_err(|e| crate::exceptions::SkyError::StorageError(e.to_string()))?;

        if !output.status.success() {
            return Err(crate::exceptions::SkyError::StorageError(
                String::from_utf8_lossy(&output.stderr).to_string()
            ));
        }

        Ok(())
    }

    // Azure Operations (stubs for now)
    async fn upload_azure(&self, _local: &Path, _remote: &str) -> Result<()> {
        Err(crate::exceptions::SkyError::NotImplemented(
            "Azure upload not yet implemented".to_string()
        ))
    }

    async fn download_azure(&self, _remote: &str, _local: &Path) -> Result<()> {
        Err(crate::exceptions::SkyError::NotImplemented(
            "Azure download not yet implemented".to_string()
        ))
    }
}
