//! Repository management

use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use git2::Repository as GitRepository;

/// Repository model
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Repository {
    pub id: i64,
    pub owner_id: i64,
    pub name: String,
    pub description: String,
    pub is_private: bool,
    pub is_fork: bool,
    pub fork_id: Option<i64>,
    pub default_branch: String,
    pub size: i64,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

impl Repository {
    /// Create new repository
    pub fn new(owner_id: i64, name: String) -> Self {
        let now = chrono::Utc::now();
        Self {
            id: 0,
            owner_id,
            name,
            description: String::new(),
            is_private: false,
            is_fork: false,
            fork_id: None,
            default_branch: "main".to_string(),
            size: 0,
            created_at: now,
            updated_at: now,
        }
    }
    
    /// Get repository path
    pub fn path(&self, root: &Path) -> PathBuf {
        root.join(format!("{}/{}.git", self.owner_id, self.name))
    }
    
    /// Initialize repository on disk
    pub fn init(&self, root: &Path) -> Result<()> {
        let path = self.path(root);
        std::fs::create_dir_all(path.parent().unwrap())?;
        
        let repo = GitRepository::init_bare(&path)?;
        
        // Set default branch
        repo.set_head(&format!("refs/heads/{}", self.default_branch))?;
        
        Ok(())
    }
    
    /// Open existing repository
    pub fn open(&self, root: &Path) -> Result<GitRepository> {
        let path = self.path(root);
        Ok(GitRepository::open(path)?)
    }
    
    /// Clone repository
    pub async fn clone_url(&self, base_url: &str) -> String {
        format!("{}/{}/{}.git", base_url, self.owner_id, self.name)
    }
    
    /// Get repository size
    pub fn calculate_size(&self, root: &Path) -> Result<i64> {
        let path = self.path(root);
        let mut size = 0;
        
        for entry in walkdir::WalkDir::new(path) {
            let entry = entry?;
            if entry.file_type().is_file() {
                size += entry.metadata()?.len() as i64;
            }
        }
        
        Ok(size)
    }
    
    /// List branches
    pub fn branches(&self, root: &Path) -> Result<Vec<String>> {
        let repo = self.open(root)?;
        let mut branches = Vec::new();
        
        for branch in repo.branches(None)? {
            let (branch, _) = branch?;
            if let Some(name) = branch.name()? {
                branches.push(name.to_string());
            }
        }
        
        Ok(branches)
    }
    
    /// List tags
    pub fn tags(&self, root: &Path) -> Result<Vec<String>> {
        let repo = self.open(root)?;
        let tags = repo.tag_names(None)?;
        
        Ok(tags.iter()
            .filter_map(|t| t.map(|s| s.to_string()))
            .collect())
    }
    
    /// Get commit count
    pub fn commit_count(&self, root: &Path) -> Result<usize> {
        let repo = self.open(root)?;
        let mut revwalk = repo.revwalk()?;
        revwalk.push_head()?;
        Ok(revwalk.count())
    }
    
    /// Get latest commits
    pub fn latest_commits(&self, root: &Path, count: usize) -> Result<Vec<CommitInfo>> {
        let repo = self.open(root)?;
        let mut revwalk = repo.revwalk()?;
        revwalk.push_head()?;
        
        let mut commits = Vec::new();
        
        for oid in revwalk.take(count) {
            let oid = oid?;
            let commit = repo.find_commit(oid)?;
            
            commits.push(CommitInfo {
                sha: oid.to_string(),
                message: commit.message().unwrap_or("").to_string(),
                author: commit.author().name().unwrap_or("").to_string(),
                email: commit.author().email().unwrap_or("").to_string(),
                timestamp: commit.time().seconds(),
            });
        }
        
        Ok(commits)
    }
}

/// Commit information
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CommitInfo {
    pub sha: String,
    pub message: String,
    pub author: String,
    pub email: String,
    pub timestamp: i64,
}

/// Repository manager
pub struct RepositoryManager {
    root: PathBuf,
}

impl RepositoryManager {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }
    
    /// Create repository
    pub async fn create(&self, repo: &Repository) -> Result<()> {
        repo.init(&self.root)?;
        Ok(())
    }
    
    /// Delete repository
    pub async fn delete(&self, repo: &Repository) -> Result<()> {
        let path = repo.path(&self.root);
        tokio::fs::remove_dir_all(path).await?;
        Ok(())
    }
    
    /// Check if repository exists
    pub fn exists(&self, repo: &Repository) -> bool {
        repo.path(&self.root).exists()
    }
}
