//! Git operations

use anyhow::Result;
use git2::Repository as GitRepository;
use std::path::Path;

/// Git operations manager
pub struct GitOps;

impl GitOps {
    /// Initialize bare repository
    pub fn init_bare(path: impl AsRef<Path>) -> Result<GitRepository> {
        std::fs::create_dir_all(&path)?;
        Ok(GitRepository::init_bare(path)?)
    }
    
    /// Clone repository
    pub fn clone(url: &str, path: impl AsRef<Path>) -> Result<GitRepository> {
        Ok(GitRepository::clone(url, path)?)
    }
    
    /// Get commit
    pub fn get_commit(repo: &GitRepository, sha: &str) -> Result<git2::Commit> {
        let oid = git2::Oid::from_str(sha)?;
        Ok(repo.find_commit(oid)?)
    }
    
    /// Get branches
    pub fn list_branches(repo: &GitRepository) -> Result<Vec<String>> {
        let mut branches = Vec::new();
        
        for branch in repo.branches(None)? {
            let (branch, _) = branch?;
            if let Some(name) = branch.name()? {
                branches.push(name.to_string());
            }
        }
        
        Ok(branches)
    }
    
    /// Get tags
    pub fn list_tags(repo: &GitRepository) -> Result<Vec<String>> {
        let tags = repo.tag_names(None)?;
        Ok(tags.iter()
            .filter_map(|t| t.map(|s| s.to_string()))
            .collect())
    }
    
    /// Get file content
    pub fn get_file(repo: &GitRepository, path: &str, reference: &str) -> Result<Vec<u8>> {
        let obj = repo.revparse_single(reference)?;
        let tree = obj.peel_to_tree()?;
        let entry = tree.get_path(Path::new(path))?;
        let blob = repo.find_blob(entry.id())?;
        Ok(blob.content().to_vec())
    }
    
    /// List directory
    pub fn list_dir(repo: &GitRepository, path: &str, reference: &str) -> Result<Vec<FileEntry>> {
        let obj = repo.revparse_single(reference)?;
        let tree = obj.peel_to_tree()?;
        
        let tree = if path.is_empty() {
            tree
        } else {
            let entry = tree.get_path(Path::new(path))?;
            repo.find_tree(entry.id())?
        };
        
        let mut entries = Vec::new();
        
        for entry in tree.iter() {
            entries.push(FileEntry {
                name: entry.name().unwrap_or("").to_string(),
                is_dir: entry.kind() == Some(git2::ObjectType::Tree),
                size: if entry.kind() == Some(git2::ObjectType::Blob) {
                    repo.find_blob(entry.id()).ok().map(|b| b.size())
                } else {
                    None
                },
            });
        }
        
        Ok(entries)
    }
}

/// File entry
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct FileEntry {
    pub name: String,
    pub is_dir: bool,
    pub size: Option<usize>,
}
