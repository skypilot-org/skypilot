#!/bin/bash
# ?? STYX - Komplettes Projekt erstellen
# Dieses Script erstellt die komplette Styx-Projektstruktur

set -e

echo "??????????????????????????????????????????????????????????????"
echo "?     ?? STYX - Projekt erstellen                           ?"
echo "??????????????????????????????????????????????????????????????"
echo ""

PROJECT_DIR="styx"

# Check if directory exists
if [ -d "$PROJECT_DIR" ]; then
    echo "??  Verzeichnis '$PROJECT_DIR' existiert bereits!"
    read -p "Soll es gel?scht werden? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -rf "$PROJECT_DIR"
        echo "? Verzeichnis gel?scht"
    else
        echo "? Abbruch"
        exit 1
    fi
fi

echo "?? Erstelle Projektstruktur..."

# Create main directory
mkdir -p $PROJECT_DIR

# Create crate directories
mkdir -p $PROJECT_DIR/crates/{core,cloud,cli,server,db,agent,ui,sdk,utils}/src

echo "? Verzeichnisse erstellt"
echo ""
echo "?? Erstelle Dateien..."

# ============================================
# ROOT Cargo.toml
# ============================================
cat > $PROJECT_DIR/Cargo.toml << 'EOF'
[workspace]
resolver = "2"
members = [
    "crates/core",
    "crates/cloud",
    "crates/cli",
    "crates/server",
    "crates/db",
    "crates/agent",
    "crates/ui",
    "crates/sdk",
    "crates/utils",
]

[workspace.package]
version = "0.1.0-alpha"
authors = ["Styx Team <engineering@styx.rs>"]
edition = "2021"
license = "Apache-2.0"
repository = "https://github.com/skypilot-org/styx"

[workspace.dependencies]
tokio = { version = "1.40", features = ["full"] }
anyhow = "1.0"
thiserror = "1.0"
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
clap = { version = "4.5", features = ["derive", "cargo", "env"] }
colored = "2.1"
axum = "0.7"
sqlx = { version = "0.8", features = ["runtime-tokio", "sqlite"] }
petgraph = "0.6"
async-trait = "0.1"
uuid = { version = "1.10", features = ["v4", "serde"] }
chrono = { version = "0.4", features = ["serde"] }

[profile.release]
opt-level = 3
lto = "fat"
codegen-units = 1
strip = true
EOF

echo "? Workspace Cargo.toml"

# ============================================
# README
# ============================================
cat > $PROJECT_DIR/README.md << 'EOF'
# ?? Styx - Cloud Orchestration Engine in Rust

High-performance, memory-safe cloud orchestration engine.

## Quick Start

```bash
cargo build --release
./target/release/styx version
```

## Features

- Multi-Cloud (AWS, GCP, K8s)
- Task DAG Scheduling
- REST API
- Web UI (WASM)
- Memory-safe, Thread-safe

## Architecture

- core: Scheduler + DAG
- cloud: Multi-cloud providers
- cli: Command-line interface
- server: REST API
- db: Database layer
- agent: Remote execution
- ui: Web UI (Leptos)

Built with ?? and Rust ??
EOF

echo "? README.md"

# ============================================
# CORE CRATE
# ============================================
cat > $PROJECT_DIR/crates/core/Cargo.toml << 'EOF'
[package]
name = "styx-core"
version.workspace = true
authors.workspace = true
edition.workspace = true
license.workspace = true

[dependencies]
tokio.workspace = true
anyhow.workspace = true
thiserror.workspace = true
serde.workspace = true
serde_json.workspace = true
tracing.workspace = true
petgraph.workspace = true
async-trait.workspace = true
uuid.workspace = true
chrono.workspace = true
EOF

cat > $PROJECT_DIR/crates/core/src/lib.rs << 'EOF'
//! Styx Core - Task Scheduler

pub mod error;
pub mod resource;
pub mod scheduler;
pub mod task;

pub use error::{Error, Result};
pub use resource::{Resource, ResourceRequirements};
pub use scheduler::{Scheduler, SchedulerConfig};
pub use task::{Task, TaskId, TaskStatus, TaskPriority};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
EOF

cat > $PROJECT_DIR/crates/core/src/error.rs << 'EOF'
//! Error types

use thiserror::Error;

#[derive(Debug, Error)]
pub enum Error {
    #[error("Not found: {0}")]
    NotFound(String),
    
    #[error("Invalid input: {0}")]
    InvalidInput(String),
    
    #[error("Internal error: {0}")]
    Internal(String),
}

pub type Result<T> = std::result::Result<T, Error>;
EOF

cat > $PROJECT_DIR/crates/core/src/task.rs << 'EOF'
//! Task definitions

use serde::{Deserialize, Serialize};
use uuid::Uuid;

pub type TaskId = Uuid;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum TaskStatus {
    Pending,
    Running,
    Completed,
    Failed(String),
    Cancelled,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
pub enum TaskPriority {
    Low,
    Normal,
    High,
    Critical,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    pub id: TaskId,
    pub name: String,
    pub command: String,
    pub args: Vec<String>,
    pub status: TaskStatus,
    pub priority: TaskPriority,
    pub dependencies: Vec<TaskId>,
}

impl Task {
    pub fn new(name: &str, command: &str) -> Self {
        Self {
            id: Uuid::new_v4(),
            name: name.to_string(),
            command: command.to_string(),
            args: Vec::new(),
            status: TaskStatus::Pending,
            priority: TaskPriority::Normal,
            dependencies: Vec::new(),
        }
    }

    pub fn with_arg(mut self, arg: String) -> Self {
        self.args.push(arg);
        self
    }

    pub fn with_priority(mut self, priority: TaskPriority) -> Self {
        self.priority = priority;
        self
    }

    pub fn with_dependency(mut self, dep: TaskId) -> Self {
        self.dependencies.push(dep);
        self
    }

    pub fn mark_running(&mut self) {
        self.status = TaskStatus::Running;
    }

    pub fn mark_completed(&mut self) {
        self.status = TaskStatus::Completed;
    }

    pub fn mark_failed(&mut self, error: &str) {
        self.status = TaskStatus::Failed(error.to_string());
    }
}
EOF

cat > $PROJECT_DIR/crates/core/src/resource.rs << 'EOF'
//! Resource definitions

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Resource {
    Cpu(f64),
    Memory(u64),
    Gpu(u32),
    Disk(u64),
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ResourceRequirements {
    pub cpu_cores: Option<f64>,
    pub memory_gb: Option<u64>,
    pub gpu_count: Option<u32>,
    pub disk_gb: Option<u64>,
}
EOF

cat > $PROJECT_DIR/crates/core/src/scheduler.rs << 'EOF'
//! Task scheduler

use crate::error::{Error, Result};
use crate::task::{Task, TaskId, TaskStatus};
use petgraph::graph::{DiGraph, NodeIndex};
use std::collections::HashMap;
use tokio::sync::RwLock;
use std::sync::Arc;

#[derive(Debug, Clone)]
pub struct SchedulerConfig {
    pub max_concurrent: usize,
}

impl Default for SchedulerConfig {
    fn default() -> Self {
        Self { max_concurrent: 10 }
    }
}

pub struct Scheduler {
    tasks: Arc<RwLock<HashMap<TaskId, Task>>>,
    dag: Arc<RwLock<DiGraph<TaskId, ()>>>,
    task_to_node: Arc<RwLock<HashMap<TaskId, NodeIndex>>>,
}

impl Scheduler {
    pub fn new(_config: SchedulerConfig) -> Self {
        Self {
            tasks: Arc::new(RwLock::new(HashMap::new())),
            dag: Arc::new(RwLock::new(DiGraph::new())),
            task_to_node: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    pub async fn submit(&self, task: Task) -> Result<TaskId> {
        let task_id = task.id;
        
        let mut tasks = self.tasks.write().await;
        tasks.insert(task_id, task.clone());

        let mut dag = self.dag.write().await;
        let mut task_to_node = self.task_to_node.write().await;
        
        let node = dag.add_node(task_id);
        task_to_node.insert(task_id, node);

        Ok(task_id)
    }

    pub async fn get_task(&self, task_id: TaskId) -> Option<Task> {
        let tasks = self.tasks.read().await;
        tasks.get(&task_id).cloned()
    }

    pub async fn get_all_tasks(&self) -> Vec<Task> {
        let tasks = self.tasks.read().await;
        tasks.values().cloned().collect()
    }

    pub async fn update_status(&self, task_id: TaskId, status: TaskStatus) -> Result<()> {
        let mut tasks = self.tasks.write().await;
        if let Some(task) = tasks.get_mut(&task_id) {
            task.status = status;
            Ok(())
        } else {
            Err(Error::NotFound(format!("Task {}", task_id)))
        }
    }

    pub async fn stats(&self) -> SchedulerStats {
        let tasks = self.tasks.read().await;
        SchedulerStats {
            total: tasks.len(),
            pending: tasks.values().filter(|t| matches!(t.status, TaskStatus::Pending)).count(),
            running: tasks.values().filter(|t| matches!(t.status, TaskStatus::Running)).count(),
            completed: tasks.values().filter(|t| matches!(t.status, TaskStatus::Completed)).count(),
            failed: tasks.values().filter(|t| matches!(t.status, TaskStatus::Failed(_))).count(),
            cancelled: tasks.values().filter(|t| matches!(t.status, TaskStatus::Cancelled)).count(),
        }
    }
}

#[derive(Debug, Default, Clone)]
pub struct SchedulerStats {
    pub total: usize,
    pub pending: usize,
    pub running: usize,
    pub completed: usize,
    pub failed: usize,
    pub cancelled: usize,
}
EOF

echo "? Core crate"

# ============================================
# CLI CRATE
# ============================================
cat > $PROJECT_DIR/crates/cli/Cargo.toml << 'EOF'
[package]
name = "styx-cli"
version.workspace = true
authors.workspace = true
edition.workspace = true
license.workspace = true

[[bin]]
name = "styx"
path = "src/main.rs"

[dependencies]
styx-core = { path = "../core" }
tokio.workspace = true
anyhow.workspace = true
clap.workspace = true
colored.workspace = true
tracing.workspace = true
tracing-subscriber.workspace = true
EOF

cat > $PROJECT_DIR/crates/cli/src/main.rs << 'EOF'
//! Styx CLI

use clap::{Parser, Subcommand};
use colored::Colorize;
use styx_core::{Scheduler, SchedulerConfig, Task, TaskPriority};

#[derive(Parser)]
#[command(name = "styx", version, about = "?? Styx - Cloud Orchestration")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    Run {
        #[arg(short, long)]
        name: String,
        command: String,
        args: Vec<String>,
    },
    Version,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Run { name, command, args } => {
            println!("{}", "?? Submitting task...".bright_cyan());
            
            let scheduler = Scheduler::new(SchedulerConfig::default());
            let mut task = Task::new(&name, &command);
            
            for arg in args {
                task = task.with_arg(arg);
            }
            
            let task_id = scheduler.submit(task).await?;
            
            println!("{}", "? Task submitted!".bright_green());
            println!("Task ID: {}", task_id.to_string().bright_yellow());
        }
        Commands::Version => {
            println!("?? Styx {}", styx_core::VERSION.bright_yellow());
            println!("Cloud Orchestration Engine in Rust");
        }
    }

    Ok(())
}
EOF

echo "? CLI crate"

# ============================================
# STUB CRATES
# ============================================
for crate in cloud server db agent ui sdk utils; do
    cat > $PROJECT_DIR/crates/$crate/Cargo.toml << EOF
[package]
name = "styx-$crate"
version.workspace = true
authors.workspace = true
edition.workspace = true
license.workspace = true

[dependencies]
styx-core = { path = "../core" }
tokio.workspace = true
anyhow.workspace = true
EOF

    cat > $PROJECT_DIR/crates/$crate/src/lib.rs << EOF
//! Styx $crate crate
//! TODO: Implement

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
EOF
done

echo "? Stub crates"

# ============================================
# .gitignore
# ============================================
cat > $PROJECT_DIR/.gitignore << 'EOF'
target/
Cargo.lock
**/*.rs.bk
.DS_Store
*.db
EOF

echo "? .gitignore"

echo ""
echo "??????????????????????????????????????????????????????????????"
echo "?     ? PROJEKT ERSTELLT!                                  ?"
echo "??????????????????????????????????????????????????????????????"
echo ""
echo "?? Verzeichnis: ./$PROJECT_DIR/"
echo ""
echo "?? Next Steps:"
echo ""
echo "  cd $PROJECT_DIR"
echo "  cargo build --release"
echo "  ./target/release/styx version"
echo ""
echo "?? Viel Erfolg mit Styx!"
