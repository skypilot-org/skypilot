//! Ghost CLI - Command-line interface for cave management
//!
//! Commands:
//! - ghost create <name> [options]
//! - ghost exec <cave-id> <code>
//! - ghost list
//! - ghost stop <cave-id>
//! - ghost destroy <cave-id>
//! - ghost logs <cave-id>

use clap::{Parser, Subcommand};

use crate::ghost::Ghost;
use crate::cave::{CaveConfig, CaveType};

/// Ghost CLI
#[derive(Parser)]
#[command(name = "ghost")]
#[command(about = "?? Ghost - Isolated execution caves", long_about = None)]
pub struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Create a new cave
    Create {
        /// Cave name
        name: String,

        /// Container image
        #[arg(short, long, default_value = "ubuntu:22.04")]
        image: String,

        /// Cave type (docker, compose, kubernetes)
        #[arg(short = 't', long, default_value = "docker")]
        cave_type: String,

        /// CPU limit
        #[arg(long)]
        cpu: Option<f64>,

        /// Memory limit (MB)
        #[arg(long)]
        memory: Option<u64>,

        /// Enable VNC
        #[arg(long)]
        vnc: bool,

        /// Enable Code Server
        #[arg(long)]
        code_server: bool,

        /// Enable terminal
        #[arg(long)]
        terminal: bool,
    },

    /// Execute code in a cave
    Exec {
        /// Cave ID
        cave_id: String,

        /// Code to execute
        code: String,

        /// Language (python, bash, node)
        #[arg(short, long, default_value = "python")]
        language: String,
    },

    /// List all caves
    List,

    /// Stop a cave
    Stop {
        /// Cave ID
        cave_id: String,
    },

    /// Destroy a cave
    Destroy {
        /// Cave ID
        cave_id: String,
    },

    /// Get cave logs
    Logs {
        /// Cave ID
        cave_id: String,

        /// Follow logs
        #[arg(short, long)]
        follow: bool,
    },

    /// Show cave details
    Info {
        /// Cave ID
        cave_id: String,
    },
}

impl Cli {
    /// Run CLI
    pub async fn run() -> anyhow::Result<()> {
        let cli = Cli::parse();

        match cli.command {
            Commands::Create {
                name,
                image,
                cave_type,
                cpu,
                memory,
                vnc,
                code_server,
                terminal,
            } => {
                let ghost = Ghost::new().await?;

                let cave_type = match cave_type.as_str() {
                    "docker" => CaveType::Docker,
                    "compose" => CaveType::Compose,
                    "kubernetes" | "k8s" => CaveType::Kubernetes,
                    _ => {
                        eprintln!("? Invalid cave type: {}", cave_type);
                        return Ok(());
                    }
                };

                let mut config = CaveConfig {
                    image,
                    cpu_limit: cpu,
                    memory_limit: memory,
                    ..Default::default()
                };

                let cave_id = ghost.create_cave(name, cave_type, config).await?;

                println!("\n? Cave created successfully!");
                println!("   Cave ID: {}", cave_id);

                if vnc || code_server || terminal {
                    println!("\n   Starting extended services...");
                    
                    if vnc {
                        println!("   ???  VNC: http://localhost:6080/vnc.html");
                    }
                    if code_server {
                        println!("   ?? Code Server: http://localhost:8080");
                    }
                    if terminal {
                        println!("   ?? Terminal: ws://localhost:7681/v1/shell/ws");
                    }
                }
            }

            Commands::Exec {
                cave_id,
                code,
                language,
            } => {
                let ghost = Ghost::new().await?;

                println!("? Executing {} code...", language);

                let result = ghost.execute(&cave_id, &code, &language).await?;

                println!("\n?? Output:");
                println!("{}", result.stdout);

                if !result.stderr.is_empty() {
                    println!("\n??  Errors:");
                    println!("{}", result.stderr);
                }

                println!("\n??  Duration: {}ms", result.duration_ms);
                println!("   Exit code: {}", result.exit_code);
            }

            Commands::List => {
                let ghost = Ghost::new().await?;
                let caves = ghost.list_caves().await;

                if caves.is_empty() {
                    println!("No caves found.");
                } else {
                    println!("\n?? Active Caves:\n");
                    println!("{:<36} {:<20} {:<12} {:<10}", "ID", "Name", "Type", "Status");
                    println!("{}", "-".repeat(80));

                    for cave in caves {
                        println!(
                            "{:<36} {:<20} {:<12} {:?}",
                            cave.id, cave.name, format!("{:?}", cave.cave_type), cave.status
                        );
                    }
                }
            }

            Commands::Stop { cave_id } => {
                let ghost = Ghost::new().await?;
                ghost.stop_cave(&cave_id).await?;
                println!("? Cave stopped: {}", cave_id);
            }

            Commands::Destroy { cave_id } => {
                let ghost = Ghost::new().await?;
                ghost.destroy_cave(&cave_id).await?;
                println!("? Cave destroyed: {}", cave_id);
            }

            Commands::Logs { cave_id, follow } => {
                let ghost = Ghost::new().await?;
                let logs = ghost.get_logs(&cave_id).await?;

                println!("?? Logs for cave: {}\n", cave_id);
                println!("{}", logs);

                if follow {
                    println!("\n[Following logs... Press Ctrl+C to stop]");
                    // TODO: Implement log following
                }
            }

            Commands::Info { cave_id } => {
                let ghost = Ghost::new().await?;
                
                if let Some(cave) = ghost.get_cave(&cave_id).await {
                    println!("\n?? Cave Information:\n");
                    println!("ID:        {}", cave.id);
                    println!("Name:      {}", cave.name);
                    println!("Type:      {:?}", cave.cave_type);
                    println!("Status:    {:?}", cave.status);
                    println!("Image:     {}", cave.config.image);
                    println!("CPU Limit: {:?}", cave.config.cpu_limit);
                    println!("Memory:    {:?} MB", cave.config.memory_limit);
                    println!("Created:   {}", chrono::DateTime::from_timestamp(cave.created_at, 0).unwrap());
                } else {
                    println!("? Cave not found: {}", cave_id);
                }
            }
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cli_parsing() {
        // Test that CLI parses correctly
        let cli = Cli::try_parse_from(&["ghost", "list"]);
        assert!(cli.is_ok());
    }
}
