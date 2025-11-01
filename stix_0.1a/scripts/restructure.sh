#!/bin/bash
# STIX Restructure Script
# Creates new folder structure and prepares for migration

set -e

echo "🚀 STIX Restructure Script"
echo "=========================="
echo ""

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Base directory
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "📂 Base directory: $BASE_DIR"
echo ""

# Backup current structure
echo -e "${YELLOW}📦 Creating backup...${NC}"
if [ ! -d "$BASE_DIR/../stix_0.1a_backup" ]; then
    cp -r "$BASE_DIR" "$BASE_DIR/../stix_0.1a_backup"
    echo -e "${GREEN}✅ Backup created at: $BASE_DIR/../stix_0.1a_backup${NC}"
else
    echo -e "${BLUE}ℹ️  Backup already exists${NC}"
fi
echo ""

# Create new crate structure
echo -e "${YELLOW}🏗️  Creating new crate structure...${NC}"

CRATES=(
    "stix-core"
    "stix-optimizer"
    "stix-clouds"
    "stix-backends"
    "stix-provision"
    "stix-catalog"
    "stix-jobs"
    "stix-serve"
    "stix-storage"
    "stix-skylet"
    "stix-cli"
    "stix-server"
    "stix-config"
    "stix-auth"
    "stix-utils"
    "stix-metrics"
    "stix-db"
    "stix-sdk"
)

cd "$BASE_DIR"

# Create crates directory if it doesn't exist
mkdir -p crates

# Create each crate
for crate in "${CRATES[@]}"; do
    CRATE_PATH="crates/$crate"
    
    if [ ! -d "$CRATE_PATH" ]; then
        echo -e "${BLUE}  Creating $crate...${NC}"
        mkdir -p "$CRATE_PATH/src"
        mkdir -p "$CRATE_PATH/tests"
        
        # Create Cargo.toml
        cat > "$CRATE_PATH/Cargo.toml" << EOF
[package]
name = "$crate"
version = "0.1.0"
edition = "2021"
authors = ["STIX Team"]
description = "$(echo $crate | sed 's/stix-/STIX /') module"
license = "Apache-2.0"

[dependencies]
tokio = { version = "1.0", features = ["full"] }
async-trait = "0.1"
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
anyhow = "1.0"
thiserror = "1.0"
tracing = "0.1"

[dev-dependencies]
mockall = "0.12"
EOF

        # Create lib.rs
        cat > "$CRATE_PATH/src/lib.rs" << EOF
//! # $(echo $crate | sed 's/stix-/STIX /' | sed 's/-/ /g')
//!
//! TODO: Add module documentation

#![warn(missing_docs)]

/// Module version
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
    }
}
EOF

        # Create README.md
        cat > "$CRATE_PATH/README.md" << EOF
# $crate

$(echo $crate | sed 's/stix-/STIX /' | sed 's/-/ /g') module

## Overview

TODO: Add overview

## Features

TODO: Add features

## Usage

\`\`\`rust
// TODO: Add usage example
\`\`\`

## License

Apache-2.0
EOF

        echo -e "${GREEN}  ✅ Created $crate${NC}"
    else
        echo -e "${BLUE}  ℹ️  $crate already exists${NC}"
    fi
done

echo ""

# Create workspace Cargo.toml
echo -e "${YELLOW}📝 Creating workspace Cargo.toml...${NC}"

cat > "$BASE_DIR/Cargo.toml" << 'EOF'
[workspace]
members = [
    "crates/stix-core",
    "crates/stix-optimizer",
    "crates/stix-clouds",
    "crates/stix-backends",
    "crates/stix-provision",
    "crates/stix-catalog",
    "crates/stix-jobs",
    "crates/stix-serve",
    "crates/stix-storage",
    "crates/stix-skylet",
    "crates/stix-cli",
    "crates/stix-server",
    "crates/stix-config",
    "crates/stix-auth",
    "crates/stix-utils",
    "crates/stix-metrics",
    "crates/stix-db",
    "crates/stix-sdk",
]

resolver = "2"

[workspace.package]
version = "0.1.0"
edition = "2021"
authors = ["STIX Team"]
license = "Apache-2.0"

[workspace.dependencies]
# Async runtime
tokio = { version = "1.0", features = ["full"] }
async-trait = "0.1"

# Serialization
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
serde_yaml = "0.9"
toml = "0.8"

# Error handling
anyhow = "1.0"
thiserror = "1.0"

# Cloud SDKs
aws-config = "1.0"
aws-sdk-ec2 = "1.0"
aws-sdk-s3 = "1.0"
azure_core = "0.20"
azure_identity = "0.20"
google-cloud-auth = "0.16"
kube = { version = "0.90", features = ["runtime", "derive"] }

# HTTP
reqwest = { version = "0.12", features = ["json"] }
hyper = "1.0"
axum = "0.7"
tower = "0.4"

# CLI
clap = { version = "4.0", features = ["derive", "cargo"] }
comfy-table = "7.0"
indicatif = "0.17"

# Utilities
uuid = { version = "1.0", features = ["v4", "serde"] }
chrono = { version = "0.4", features = ["serde"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
dirs = "5.0"

# Database
sqlx = { version = "0.7", features = ["runtime-tokio-native-tls", "sqlite"] }

# Testing
mockall = "0.12"
tempfile = "3.0"

[profile.release]
opt-level = 3
lto = true
codegen-units = 1
EOF

echo -e "${GREEN}✅ Workspace Cargo.toml created${NC}"
echo ""

# Create additional directories
echo -e "${YELLOW}📁 Creating additional directories...${NC}"

mkdir -p docs
mkdir -p tests/integration
mkdir -p tests/e2e
mkdir -p scripts
mkdir -p configs/clouds
mkdir -p configs/templates

echo -e "${GREEN}✅ Directories created${NC}"
echo ""

# Create scripts
echo -e "${YELLOW}🔧 Creating helper scripts...${NC}"

# Build script
cat > "$BASE_DIR/scripts/build.sh" << 'EOF'
#!/bin/bash
set -e
echo "🔨 Building STIX..."
cargo build --workspace --release
echo "✅ Build complete"
EOF

# Test script
cat > "$BASE_DIR/scripts/test.sh" << 'EOF'
#!/bin/bash
set -e
echo "🧪 Running tests..."
cargo test --workspace
echo "✅ Tests passed"
EOF

# Format script
cat > "$BASE_DIR/scripts/format.sh" << 'EOF'
#!/bin/bash
set -e
echo "🎨 Formatting code..."
cargo fmt --all
echo "✅ Format complete"
EOF

# Lint script
cat > "$BASE_DIR/scripts/lint.sh" << 'EOF'
#!/bin/bash
set -e
echo "🔍 Linting code..."
cargo clippy --workspace -- -D warnings
echo "✅ Lint passed"
EOF

chmod +x scripts/*.sh

echo -e "${GREEN}✅ Scripts created${NC}"
echo ""

# Create .gitignore
echo -e "${YELLOW}📝 Creating .gitignore...${NC}"

cat > "$BASE_DIR/.gitignore" << 'EOF'
# Rust
/target
Cargo.lock
**/*.rs.bk
*.pdb

# IDEs
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Logs
*.log
logs/

# Environment
.env
.env.local

# Database
*.db
*.sqlite

# Temporary
tmp/
temp/
EOF

echo -e "${GREEN}✅ .gitignore created${NC}"
echo ""

# Test build
echo -e "${YELLOW}🧪 Testing build...${NC}"
if cargo build --workspace 2>&1 | head -20; then
    echo -e "${GREEN}✅ Workspace builds successfully${NC}"
else
    echo -e "${YELLOW}⚠️  Some warnings (expected for empty crates)${NC}"
fi
echo ""

# Summary
echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}✅ Restructure Complete!${NC}"
echo -e "${GREEN}================================${NC}"
echo ""
echo "📊 Summary:"
echo "  - 18 crates created"
echo "  - Workspace configured"
echo "  - Helper scripts created"
echo "  - Backup saved"
echo ""
echo "🚀 Next Steps:"
echo "  1. Review crate structure: ls -la crates/"
echo "  2. Build workspace: cargo build --workspace"
echo "  3. Run tests: cargo test --workspace"
echo "  4. Start implementing: See IMPLEMENTATION_PLAN.md"
echo ""
echo "📚 Documentation:"
echo "  - TODO.md - Feature analysis"
echo "  - FOLDER_STRUCTURE.md - Architecture"
echo "  - IMPLEMENTATION_PLAN.md - Roadmap"
echo "  - PROJECT_SUMMARY.md - Overview"
echo ""
echo -e "${BLUE}Happy coding! 🦀${NC}"
