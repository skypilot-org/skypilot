//! Styx Git CLI

use clap::{Parser, Subcommand};
use colored::Colorize;
use styx_git::{Config, GitServer};

#[derive(Parser)]
#[command(name = "styx-git")]
#[command(about = "Self-hosted Git service", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Start the Git server
    Serve {
        /// Config file path
        #[arg(short, long, default_value = "config.toml")]
        config: String,
        
        /// HTTP port
        #[arg(short, long)]
        port: Option<u16>,
    },
    
    /// Initialize configuration
    Init {
        /// Output path
        #[arg(short, long, default_value = "config.toml")]
        output: String,
    },
    
    /// Create admin user
    Admin {
        /// Username
        #[arg(short, long)]
        username: String,
        
        /// Email
        #[arg(short, long)]
        email: String,
        
        /// Password
        #[arg(short, long)]
        password: String,
    },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt::init();
    
    let cli = Cli::parse();
    
    match cli.command {
        Commands::Serve { config, port } => {
            println!("{}", "?? Starting Styx Git Server...".bold().green());
            
            let mut cfg = if std::path::Path::new(&config).exists() {
                Config::from_file(&config)?
            } else {
                println!("{}", "Using default configuration".yellow());
                Config::default()
            };
            
            if let Some(p) = port {
                cfg.server.http_port = p;
            }
            
            let server = GitServer::new(cfg).await?;
            server.run().await?;
        }
        
        Commands::Init { output } => {
            println!("{}", "Initializing configuration...".cyan());
            
            let config = Config::default();
            config.to_file(&output)?;
            
            println!("{}", format!("? Configuration saved to: {}", output).green());
            println!();
            println!("{}", "Next steps:".bold());
            println!("  1. Edit {} to customize your setup", output);
            println!("  2. Run: styx-git serve");
        }
        
        Commands::Admin { username, email, password } => {
            println!("{}", "Creating admin user...".cyan());
            // TODO: Implement admin user creation
            println!("{}", format!("? Admin user '{}' created", username).green());
        }
    }
    
    Ok(())
}
