//! Styx Command Line Interface

use clap::{Parser, Subcommand};
use colored::Colorize;
use styx_core::{Scheduler, SchedulerConfig, Task, TaskPriority};
use tracing::{info, Level};
use tracing_subscriber;

#[derive(Parser)]
#[command(name = "styx")]
#[command(author = "Styx Team")]
#[command(version = styx_core::VERSION)]
#[command(about = "?? Styx - Cloud Orchestration in Rust", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,

    /// Enable verbose logging
    #[arg(short, long, global = true)]
    verbose: bool,
}

#[derive(Subcommand)]
enum Commands {
    /// Submit and run a task
    Run {
        /// Task name
        #[arg(short, long)]
        name: String,

        /// Command to execute
        command: String,

        /// Command arguments
        args: Vec<String>,

        /// Task priority (low, normal, high, critical)
        #[arg(short, long, default_value = "normal")]
        priority: String,
    },

    /// Show task status
    Status {
        /// Task ID (optional, shows all if omitted)
        task_id: Option<String>,
    },

    /// Show scheduler statistics
    Stats,

    /// Show version information
    Version,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    // Setup logging
    let level = if cli.verbose {
        Level::DEBUG
    } else {
        Level::INFO
    };

    tracing_subscriber::fmt()
        .with_max_level(level)
        .with_target(false)
        .init();

    // Handle commands
    match cli.command {
        Commands::Run {
            name,
            command,
            args,
            priority,
        } => {
            run_task(name, command, args, priority).await?;
        }
        Commands::Status { task_id } => {
            show_status(task_id).await?;
        }
        Commands::Stats => {
            show_stats().await?;
        }
        Commands::Version => {
            show_version();
        }
    }

    Ok(())
}

async fn run_task(
    name: String,
    command: String,
    args: Vec<String>,
    priority_str: String,
) -> anyhow::Result<()> {
    let priority = match priority_str.to_lowercase().as_str() {
        "low" => TaskPriority::Low,
        "normal" => TaskPriority::Normal,
        "high" => TaskPriority::High,
        "critical" => TaskPriority::Critical,
        _ => TaskPriority::Normal,
    };

    println!("{}", "?? Submitting task...".bright_cyan().bold());
    println!("  {} {}", "Name:".bright_white(), name);
    println!("  {} {}", "Command:".bright_white(), command);
    if !args.is_empty() {
        println!("  {} {:?}", "Args:".bright_white(), args);
    }
    println!("  {} {:?}", "Priority:".bright_white(), priority);
    println!();

    // Create scheduler
    let scheduler = Scheduler::new(SchedulerConfig::default());

    // Create task
    let mut task = Task::new(&name, &command).with_priority(priority);

    for arg in args {
        task = task.with_arg(arg);
    }

    // Submit task
    let task_id = scheduler.submit(task).await?;

    info!("Task submitted with ID: {}", task_id);

    println!("{}", "? Task submitted successfully!".bright_green().bold());
    println!(
        "  {} {}",
        "Task ID:".bright_white(),
        task_id.to_string().bright_yellow()
    );
    println!();
    println!(
        "  Run {} to check status",
        format!("styx status {}", task_id).bright_cyan()
    );

    Ok(())
}

async fn show_status(task_id: Option<String>) -> anyhow::Result<()> {
    let scheduler = Scheduler::new(SchedulerConfig::default());

    println!("{}", "?? Task Status".bright_cyan().bold());
    println!();

    if let Some(id_str) = task_id {
        println!(
            "  {} Not yet implemented: single task status",
            "??".yellow()
        );
        println!("  Task ID: {}", id_str);
    } else {
        let tasks = scheduler.get_all_tasks().await;

        if tasks.is_empty() {
            println!("  {} No tasks found", "??".bright_blue());
        } else {
            for task in tasks {
                let status_str = match task.status {
                    styx_core::TaskStatus::Pending => "PENDING".yellow(),
                    styx_core::TaskStatus::Running => "RUNNING".bright_cyan(),
                    styx_core::TaskStatus::Completed => "COMPLETED".bright_green(),
                    styx_core::TaskStatus::Failed(_) => "FAILED".bright_red(),
                    styx_core::TaskStatus::Cancelled => "CANCELLED".bright_black(),
                };

                println!("  {} {} - {}", task.id, task.name, status_str);
            }
        }
    }

    Ok(())
}

async fn show_stats() -> anyhow::Result<()> {
    let scheduler = Scheduler::new(SchedulerConfig::default());
    let stats = scheduler.stats().await;

    println!("{}", "?? Scheduler Statistics".bright_cyan().bold());
    println!();
    println!("  {} {}", "Total Tasks:".bright_white(), stats.total);
    println!("  {} {}", "Pending:".yellow(), stats.pending);
    println!("  {} {}", "Running:".bright_cyan(), stats.running);
    println!("  {} {}", "Completed:".bright_green(), stats.completed);
    println!("  {} {}", "Failed:".bright_red(), stats.failed);
    println!("  {} {}", "Cancelled:".bright_black(), stats.cancelled);

    Ok(())
}

fn show_version() {
    println!();
    println!(
        "  {} {}",
        "?? Styx".bright_cyan().bold(),
        styx_core::VERSION.bright_yellow()
    );
    println!();
    println!(
        "  {} Cloud Orchestration Engine in Rust",
        "?".bright_white()
    );
    println!(
        "  {} https://github.com/skypilot-org/styx",
        "?".bright_white()
    );
    println!();
    println!("  {} High-performance task scheduling", "?".bright_green());
    println!("  {} Multi-cloud resource management", "?".bright_green());
    println!("  {} Memory-safe & thread-safe", "?".bright_green());
    println!();
}
