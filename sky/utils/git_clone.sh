#!/bin/bash

# Git clone script for SkyPilot remote clusters
# This script handles Git repository cloning with authentication and different ref types

set -e  # Exit on any error

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >&2
}

# Function to check and install Git if not present
check_and_install_git() {
    # Check if git is already installed
    if command -v git >/dev/null 2>&1; then
        log "Git is already installed: $(git --version)"
        return 0
    fi
    
    log "Git not found, attempting to install..."
    
    # Function to try installing with error handling
    install_with_error_handling() {
        local cmd_with_sudo="$1"
        local cmd_without_sudo="$2"
        local pkg_manager="$3"
        
        log "Trying to install Git using $pkg_manager..."
        
        # First, try with sudo
        log "Attempting with sudo..."
        if eval "$cmd_with_sudo" 2>&1 >/tmp/git_install.log; then
            log "Installation with sudo completed successfully"
            rm -f /tmp/git_install.log
            return 0
        else
            log "Installation with sudo failed, trying without sudo..."
            cat /tmp/git_install.log >&2
            rm -f /tmp/git_install.log
        fi
        
        # If sudo failed, try without sudo
        log "Attempting without sudo..."
        if eval "$cmd_without_sudo" 2>&1 >/tmp/git_install.log; then
            log "Installation without sudo completed successfully"
            rm -f /tmp/git_install.log
            return 0
        else
            log "Installation failed. Error output:"
            cat /tmp/git_install.log >&2
            
            # Check for common error patterns
            if grep -q -i "permission\|denied\|unauthorized" /tmp/git_install.log; then
                log "ERROR: Installation failed due to permission issues"
                log "Current user: $(whoami), UID: $(id -u)"
                log "Try running this script as root or with appropriate permissions"
            elif grep -q -i "command not found\|not found" /tmp/git_install.log; then
                log "ERROR: Package manager command not found"
                log "Please install Git manually using your system's package manager"
            fi
            
            rm -f /tmp/git_install.log
            return 1
        fi
    }
    
    # Detect the operating system and package manager
    if [ -f /etc/debian_version ]; then
        # Debian/Ubuntu
        log "Detected Debian/Ubuntu system"
        if command -v apt-get >/dev/null 2>&1; then
            install_with_error_handling "sudo apt-get update -y && sudo apt-get install -y git" "apt-get update -y && apt-get install -y git" "apt-get"
        elif command -v apt >/dev/null 2>&1; then
            install_with_error_handling "sudo apt update -y && sudo apt install -y git" "apt update -y && apt install -y git" "apt"
        else
            log "ERROR: Neither apt-get nor apt found on Debian/Ubuntu system"
            return 1
        fi
    elif [ -f /etc/redhat-release ] || [ -f /etc/centos-release ]; then
        # Red Hat/CentOS/Fedora
        log "Detected Red Hat/CentOS/Fedora system"
        if command -v dnf >/dev/null 2>&1; then
            install_with_error_handling "sudo dnf install -y git" "dnf install -y git" "dnf"
        elif command -v yum >/dev/null 2>&1; then
            install_with_error_handling "sudo yum install -y git" "yum install -y git" "yum"
        else
            log "ERROR: Neither dnf nor yum found on Red Hat/CentOS/Fedora system"
            return 1
        fi
    elif [ -f /etc/arch-release ]; then
        # Arch Linux
        log "Detected Arch Linux system"
        if command -v pacman >/dev/null 2>&1; then
            install_with_error_handling "sudo pacman -S --noconfirm git" "pacman -S --noconfirm git" "pacman"
        else
            log "ERROR: pacman not found on Arch Linux system"
            return 1
        fi
    elif [ -f /etc/alpine-release ]; then
        # Alpine Linux
        log "Detected Alpine Linux system"
        if command -v apk >/dev/null 2>&1; then
            install_with_error_handling "sudo apk add --no-cache git" "apk add --no-cache git" "apk"
        else
            log "ERROR: apk not found on Alpine Linux system"
            return 1
        fi
    elif [ "$(uname)" = "Darwin" ]; then
        # macOS
        log "Detected macOS system"
        if command -v brew >/dev/null 2>&1; then
            install_with_error_handling "brew install git" "brew install git" "Homebrew"
        elif command -v port >/dev/null 2>&1; then
            install_with_error_handling "sudo port install git" "port install git" "MacPorts"
        else
            log "ERROR: Neither Homebrew nor MacPorts found on macOS"
            log "Please install Git manually: https://git-scm.com/download/mac"
            return 1
        fi
    else
        log "ERROR: Unsupported operating system: $(uname -s)"
        log "Please install Git manually: https://git-scm.com/downloads"
        return 1
    fi
    
    # Verify installation
    if command -v git >/dev/null 2>&1; then
        log "Git successfully installed: $(git --version)"
        return 0
    else
        log "ERROR: Git installation failed or Git is not in PATH"
        log "Please install Git manually and ensure it's in your PATH"
        return 1
    fi
}

# Function to cleanup temporary files
cleanup() {
    if [ -n "$SSH_KEY_FILE" ] && [ -f "$SSH_KEY_FILE" ]; then
        log "Cleaning up temporary SSH key file"
        rm -f "$SSH_KEY_FILE"
    fi
}

# Function to normalize Git URL for comparison and extract schema
# Note: URLs from GitRepo are already standardized as HTTPS or SSH format
# Returns: "normalized_url schema"
normalize_git_url() {
    local url="$1"
    local schema=""
    
    # Remove trailing .git and /
    url=$(echo "$url" | sed 's|\.git/*$||' | sed 's|/*$||')
    
    # Convert standardized formats to host/path for comparison and extract schema
    if [[ "$url" =~ ^ssh://([^@]+@)?([^:/]+)(:([0-9]+))?/(.+)$ ]]; then
        # SSH standard format: ssh://user@host:port/path -> host/path
        local host="${BASH_REMATCH[2]}"
        local path="${BASH_REMATCH[5]}"
        schema="ssh"
        echo "$host/$path $schema"
    elif [[ "$url" =~ ^https?://([^@]*@)?([^:/]+)(:([0-9]+))?/(.+)$ ]]; then
        # HTTPS format: https://token@host:port/path -> host/path
        local host="${BASH_REMATCH[2]}"
        local path="${BASH_REMATCH[5]}"
        schema="https"
        echo "$host/$path $schema"
    else
        # Fallback: return as-is (shouldn't happen with standardized URLs)
        schema="unknown"
        echo "$url $schema"
    fi
}

# Set up cleanup trap
trap cleanup EXIT

# Check if target directory is provided
if [ -z "$1" ]; then
    log "ERROR: Target directory not provided"
    echo "Usage: $0 <target_directory>"
    exit 1
fi

TARGET_DIR="$1"
log "Target directory: $TARGET_DIR"

# Check if GIT_URL is provided
if [ -z "$GIT_URL" ]; then
    log "GIT_URL environment variable not set, skipping git clone"
    exit 0
fi

log "Git URL: $GIT_URL"

# Check and install Git if not present
if ! check_and_install_git; then
    log "ERROR: Failed to ensure Git is installed"
    exit 1
fi

# Setup SSH key if provided
SSH_KEY_FILE=""
if [ -n "$GIT_SSH_KEY" ]; then
    log "Setting up SSH key authentication"
    SSH_KEY_FILE=$(mktemp)
    echo "$GIT_SSH_KEY" > "$SSH_KEY_FILE"
    chmod 600 "$SSH_KEY_FILE"
    
    # Setup Git SSH command
    export GIT_SSH_COMMAND="ssh -i $SSH_KEY_FILE -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes"
    log "SSH key written to temporary file: $SSH_KEY_FILE"
fi

# Setup token authentication if provided
CLONE_URL="$GIT_URL"
if [ -n "$GIT_TOKEN" ]; then
    log "Setting up token authentication"
    if [[ "$GIT_URL" =~ ^https://(.*)$ ]]; then
        # For container environments, directly embed token in URL for reliability
        # This is simpler and more reliable than credential helper in containers
        CLONE_URL="https://${GIT_TOKEN}@${BASH_REMATCH[1]}"
        log "Token authentication configured via URL embedding"
    else
        log "WARNING: GIT_TOKEN provided but URL is not HTTPS, ignoring token"
    fi
fi

# Determine reference type and clone strategy
REF_TYPE=""
REF_VALUE=""
CLONE_ARGS=""

if [ -n "$GIT_BRANCH" ]; then
    REF_TYPE="branch"
    REF_VALUE="$GIT_BRANCH"
    CLONE_ARGS="--depth 1 --branch $GIT_BRANCH"
    log "Using branch: $GIT_BRANCH (shallow clone)"
elif [ -n "$GIT_TAG" ]; then
    REF_TYPE="tag"
    REF_VALUE="$GIT_TAG"
    CLONE_ARGS="--depth 1 --branch $GIT_TAG"
    log "Using tag: $GIT_TAG (shallow clone)"
elif [ -n "$GIT_COMMIT_HASH" ]; then
    REF_TYPE="commit"
    REF_VALUE="$GIT_COMMIT_HASH"
    # For commit hash, we need full clone first, then checkout
    CLONE_ARGS=""
    log "Using commit hash: $GIT_COMMIT_HASH (full clone required)"
else
    # Use git default behavior to clone the default branch
    REF_TYPE="default"
    # Get the remote default branch name before cloning
    log "Determining remote default branch..."
    if REF_VALUE=$(git ls-remote --symref "$CLONE_URL" HEAD 2>/dev/null | grep '^ref:' | sed 's/^ref: refs\/heads\///' | awk '{print $1}' | head -1); then
        if [ -n "$REF_VALUE" ]; then
            CLONE_ARGS="--depth 1 --branch $REF_VALUE"
            log "Remote default branch: $REF_VALUE (shallow clone)"
        else
            REF_VALUE=""  # Will be determined after clone
            CLONE_ARGS="--depth 1"
            log "Could not determine remote default branch, using git default behavior"
        fi
    else
        REF_VALUE=""  # Will be determined after clone
        CLONE_ARGS="--depth 1"
        log "Could not query remote, using git default behavior"
    fi
fi

# Check if target directory already exists and has git repository
if [ -d "$TARGET_DIR" ]; then
    if [ -d "$TARGET_DIR/.git" ]; then
        log "Git repository already exists, updating..."
        cd "$TARGET_DIR"
        
        # Verify this is the same repository
        CURRENT_REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
        if [ -n "$CURRENT_REMOTE" ]; then
            # Normalize URLs for comparison (handles HTTPS, SSH formats)
            CURRENT_RESULT=$(normalize_git_url "$CURRENT_REMOTE")
            TARGET_RESULT=$(normalize_git_url "$GIT_URL")
            
            # Extract normalized URL and schema
            CURRENT_CLEAN=$(echo "$CURRENT_RESULT" | cut -d' ' -f1)
            CURRENT_SCHEMA=$(echo "$CURRENT_RESULT" | cut -d' ' -f2)
            TARGET_CLEAN=$(echo "$TARGET_RESULT" | cut -d' ' -f1)
            TARGET_SCHEMA=$(echo "$TARGET_RESULT" | cut -d' ' -f2)
            
            if [ "$CURRENT_CLEAN" != "$TARGET_CLEAN" ]; then
                log "WARNING: Existing repository has different remote URL"
                log "Current: $CURRENT_REMOTE -> $CURRENT_CLEAN ($CURRENT_SCHEMA)"
                log "Target: $GIT_URL -> $TARGET_CLEAN ($TARGET_SCHEMA)"
                log "Please manually remove the directory or use a different target directory."
                exit 1
            elif [ "$CURRENT_SCHEMA" != "$TARGET_SCHEMA" ]; then
                # Same repository but different schema (HTTPS vs SSH)
                log "Repository matches but schema differs"
                log "Current: $CURRENT_REMOTE ($CURRENT_SCHEMA)"
                log "Target: $GIT_URL ($TARGET_SCHEMA)"
                log "Updating remote URL to match authentication method"
                git remote set-url origin "$CLONE_URL"
                log "Remote URL updated successfully"
                NEED_CLONE=false
            else
                log "Repository remote URL matches exactly, fetching updates"
                NEED_CLONE=false
            fi
        else
            log "Cannot determine remote URL, assuming repository needs update"
            NEED_CLONE=false
        fi
    else
        log "Directory exists but is not a git repository"
        cd "$TARGET_DIR"
        NEED_CLONE=true
    fi
else
    log "Creating target directory: $TARGET_DIR"
    mkdir -p "$TARGET_DIR"
    cd "$TARGET_DIR"
    NEED_CLONE=true
fi

# Clone or update repository
if [ "$NEED_CLONE" = true ]; then
    log "Cloning repository..."
    if [ "$REF_TYPE" = "commit" ]; then
        # Clone full repository for commit hash
        git clone "$CLONE_URL" .
        log "Successfully cloned repository"
    else
        # Clone with appropriate arguments (branch-specific, tag-specific, or default)
        git clone $CLONE_ARGS "$CLONE_URL" .
        if [ "$REF_TYPE" = "branch" ]; then
            log "Successfully cloned branch: $REF_VALUE"
        elif [ "$REF_TYPE" = "tag" ]; then
            log "Successfully cloned tag: $REF_VALUE"
        else
            # Determine the actual default branch name (if not already known)
            if [ -z "$REF_VALUE" ]; then
                REF_VALUE=$(git branch --show-current)
            fi
            log "Successfully cloned default branch: $REF_VALUE"
        fi
    fi
else
    # Update existing repository
    log "Fetching updates..."
    
    if [ "$REF_TYPE" = "commit" ]; then
        # For commit hash, we need to fetch all refs
        # Check if this is a shallow repository
        if [ -f ".git/shallow" ]; then
            log "Detected shallow repository, converting to full clone for commit access"
            git fetch --unshallow
        else
            git fetch origin
        fi
    elif [ "$REF_TYPE" = "tag" ]; then
        # For specific tag, fetch that tag
        if ! git fetch origin "+refs/tags/$REF_VALUE:refs/tags/$REF_VALUE"; then
            log "ERROR: Failed to fetch tag '$REF_VALUE' from remote"
            log "This could mean:"
            log "  1. Remote tag '$REF_VALUE' does not exist"
            log "  2. Network connectivity issues"
            log "  3. Authentication problems"
            log "Checking available remote tags..."
            git ls-remote --tags origin | sed 's/.*refs\/tags\//  - /' || true
            exit 1
        fi
    elif [ "$REF_TYPE" = "default" ]; then
        # For default branch, get current branch and fetch it (if not already known)
        if [ -z "$REF_VALUE" ]; then
            REF_VALUE=$(git branch --show-current)
        fi
        log "Current default branch: $REF_VALUE"
        if ! git fetch origin "+refs/heads/$REF_VALUE:refs/remotes/origin/$REF_VALUE"; then
            log "ERROR: Failed to fetch default branch '$REF_VALUE' from remote"
            log "This could mean the remote branch no longer exists"
            exit 1
        fi
    else
        # For specific branch, fetch that branch and create remote tracking branch
        if ! git fetch origin "+refs/heads/$REF_VALUE:refs/remotes/origin/$REF_VALUE"; then
            log "ERROR: Failed to fetch branch '$REF_VALUE' from remote"
            log "This could mean:"
            log "  1. Remote branch '$REF_VALUE' does not exist"
            log "  2. Network connectivity issues"
            log "  3. Authentication problems"
            log "Checking available remote branches..."
            git ls-remote --heads origin | sed 's/.*refs\/heads\//  - /' || true
            exit 1
        fi
    fi
    log "Successfully fetched updates"
fi

# Checkout the desired reference
if [ "$REF_TYPE" = "commit" ]; then
    # Checkout specific commit
    log "Checking out commit: $REF_VALUE"
    if ! git checkout "$REF_VALUE"; then
        log "ERROR: Failed to checkout commit '$REF_VALUE'"
        log "This could mean:"
        log "  1. The commit hash does not exist in the repository"
        log "  2. The commit hash is incomplete or invalid"
        log "  3. The repository may still be missing some history"
        log "Try providing a longer commit hash or ensure the commit exists"
        exit 1
    fi
    log "Successfully checked out commit: $REF_VALUE"
elif [ "$REF_TYPE" = "tag" ]; then
    # Checkout specific tag
    log "Checking out tag: $REF_VALUE"
    if ! git checkout "$REF_VALUE"; then
        log "ERROR: Failed to checkout tag '$REF_VALUE'"
        log "This could mean:"
        log "  1. The tag '$REF_VALUE' does not exist in the repository"
        log "  2. The tag name is invalid"
        log "  3. Local repository is in an inconsistent state"
        exit 1
    fi
    log "Successfully checked out tag: $REF_VALUE"
else
    # Checkout specific branch
    log "Checking out branch: $REF_VALUE"
    if git checkout "$REF_VALUE" 2>/dev/null; then
        log "Switched to existing local branch: $REF_VALUE"
    elif git checkout -b "$REF_VALUE" "origin/$REF_VALUE" 2>/dev/null; then
        log "Created new local branch tracking origin/$REF_VALUE"
    else
        log "ERROR: Failed to checkout branch '$REF_VALUE'"
        log "This could mean:"
        log "  1. Remote branch 'origin/$REF_VALUE' does not exist"
        log "  2. Local repository is in an inconsistent state"
        exit 1
    fi
    
    # Ensure we're on the latest commit of the branch
    if [ "$NEED_CLONE" != true ]; then
        git reset --hard "origin/$REF_VALUE"
    fi
    log "Successfully checked out branch: $REF_VALUE"
fi

# Verify final state
CURRENT_REF=$(git rev-parse HEAD)
log "Repository is now at commit: $CURRENT_REF"

if [ "$REF_TYPE" = "branch" ] || [ "$REF_TYPE" = "default" ]; then
    CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "detached")
    log "Current branch: $CURRENT_BRANCH"
elif [ "$REF_TYPE" = "tag" ]; then
    CURRENT_TAG=$(git describe --tags --exact-match 2>/dev/null || echo "unknown")
    log "Current tag: $CURRENT_TAG"
fi

log "Git clone/update completed successfully"
