//! Storage Example
//!
//! SkyPilot: sky.Storage API

use styx_core::{Storage, StorageType, StorageMode, StorageOps};
use std::path::PathBuf;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Python equivalent:
    // import sky
    // 
    // storage = sky.Storage(
    //     name='my-data',
    //     source='~/local/data',
    //     mount='/remote/data'
    // )
    // 
    // # Upload files
    // storage.upload(['file1.txt', 'file2.txt'])
    // 
    // # Download files
    // storage.download('/tmp/downloaded')

    // Create storage
    let storage = Storage::new("my-data", StorageType::S3)
        .with_source("~/local/data")
        .with_mount("/remote/data")
        .with_mode(StorageMode::Mount)
        .with_persistent(true);

    println!("? Storage created:");
    println!("   Name: {}", storage.name);
    println!("   Type: {:?}", storage.storage_type);
    println!("   Source: {:?}", storage.source);
    println!("   Mount: {:?}", storage.mount);
    println!("   Mode: {:?}", storage.mode);
    println!("   Persistent: {}", storage.persistent);

    // Upload files
    let files = vec![
        PathBuf::from("file1.txt"),
        PathBuf::from("file2.txt"),
    ];
    StorageOps::upload(&storage, files).await?;
    println!("\n? Files uploaded");

    // Download files
    StorageOps::download(&storage, PathBuf::from("/tmp/downloaded")).await?;
    println!("? Files downloaded to /tmp/downloaded");

    // List all storages
    let all_storages = StorageOps::list().await?;
    println!("\n?? Total storages: {}", all_storages.len());

    Ok(())
}
