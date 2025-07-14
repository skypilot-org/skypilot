#!/bin/bash

# Git clone script for SkyPilot remote clusters
# This script handles Git repository cloning with authentication and different ref types

set -e  # Exit on any error
set -x

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >&2
}

# Function to cleanup temporary files
cleanup() {
    if [ -n "$SSH_KEY_FILE" ] && [ -f "$SSH_KEY_FILE" ]; then
        log "Cleaning up temporary SSH key file"
        rm -f "$SSH_KEY_FILE"
    fi
}

# Function to normalize Git URL for comparison
# Note: URLs from GitRepo are already standardized as HTTPS or SSH format
normalize_git_url() {
    local url="$1"
    
    # Remove trailing .git and /
    url=$(echo "$url" | sed 's|\.git/*$||' | sed 's|/*$||')
    
    # Convert standardized formats to host/path for comparison
    if [[ "$url" =~ ^ssh://([^@]+@)?([^:/]+)(:([0-9]+))?/(.+)$ ]]; then
        # SSH standard format: ssh://user@host:port/path -> host/path
        local host="${BASH_REMATCH[2]}"
        local path="${BASH_REMATCH[5]}"
        echo "$host/$path"
    elif [[ "$url" =~ ^https?://([^@]*@)?([^:/]+)(:([0-9]+))?/(.+)$ ]]; then
        # HTTPS format: https://token@host:port/path -> host/path
        local host="${BASH_REMATCH[2]}"
        local path="${BASH_REMATCH[5]}"
        echo "$host/$path"
    else
        # Fallback: return as-is (shouldn't happen with standardized URLs)
        echo "$url"
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
        CLONE_URL="$GIT_URL"  # Keep original URL, don't embed token
        
        # Use git credential helper for secure token authentication
        # GitHub requires personal access token, not password authentication
        
        # Create a temporary credential helper script
        CRED_HELPER_SCRIPT=$(mktemp)
        cat > "$CRED_HELPER_SCRIPT" << EOF
#!/bin/bash
# Temporary credential helper for GitHub token authentication
case "\$1" in
    get)
        echo "username=token"
        echo "password=${GIT_TOKEN}"
        ;;
    store|erase)
        # Do nothing for store/erase operations
        ;;
esac
EOF
        chmod +x "$CRED_HELPER_SCRIPT"
        
        # Configure git to use our credential helper for this session
        export GIT_CONFIG_GLOBAL=/dev/null
        export GIT_CONFIG_SYSTEM=/dev/null
        git config --global credential.helper "!$CRED_HELPER_SCRIPT"
        
        # Clean up function for credential helper
        cleanup_cred_helper() {
            if [ -f "$CRED_HELPER_SCRIPT" ]; then
                rm -f "$CRED_HELPER_SCRIPT"
            fi
            # Reset git config
            git config --global --unset credential.helper 2>/dev/null || true
        }
        
        # Add to cleanup trap
        trap 'cleanup_cred_helper; cleanup' EXIT
        
        log "Token authentication configured via Git credential helper"
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
elif [ -n "$GIT_COMMIT_HASH" ]; then
    REF_TYPE="commit"
    REF_VALUE="$GIT_COMMIT_HASH"
    # For commit hash, we need full clone first, then checkout
    CLONE_ARGS=""
    log "Using commit hash: $GIT_COMMIT_HASH (full clone required)"
else
    # Use git default behavior to clone the default branch
    REF_TYPE="default"
    REF_VALUE=""  # Will be determined after clone
    CLONE_ARGS="--depth 1"
    log "No specific ref provided, using git default branch (shallow clone)"
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
            CURRENT_CLEAN=$(normalize_git_url "$CURRENT_REMOTE")
            TARGET_CLEAN=$(normalize_git_url "$GIT_URL")
            
            if [ "$CURRENT_CLEAN" != "$TARGET_CLEAN" ]; then
                log "WARNING: Existing repository has different remote URL"
                log "Current: $CURRENT_REMOTE -> $CURRENT_CLEAN"
                log "Target: $GIT_URL -> $TARGET_CLEAN"
                log "Removing existing directory and re-cloning"
                cd ..
                rm -rf "$TARGET_DIR"
                mkdir -p "$TARGET_DIR"
                cd "$TARGET_DIR"
                NEED_CLONE=true
            else
                log "Repository remote URL matches, fetching updates"
                NEED_CLONE=false
            fi
        else
            log "Cannot determine remote URL, assuming repository needs update"
            NEED_CLONE=false
        fi
    else
        log "Directory exists but is not a git repository, cleaning up"
        rm -rf "$TARGET_DIR"
        mkdir -p "$TARGET_DIR"
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
        # Clone with appropriate arguments (branch-specific or default)
        git clone $CLONE_ARGS "$CLONE_URL" .
        if [ "$REF_TYPE" = "branch" ]; then
            log "Successfully cloned branch: $REF_VALUE"
        else
            # Determine the actual default branch name
            REF_VALUE=$(git branch --show-current)
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
    elif [ "$REF_TYPE" = "default" ]; then
        # For default branch, get current branch and fetch it
        REF_VALUE=$(git branch --show-current)
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
elif [ "$REF_TYPE" = "default" ]; then
    # For default branch, we're already on the correct branch
    log "Using default branch: $REF_VALUE"
    if [ "$NEED_CLONE" != true ]; then
        # Ensure we're on the latest commit of the default branch
        git reset --hard "origin/$REF_VALUE"
        log "Updated to latest commit of default branch: $REF_VALUE"
    fi
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
fi

log "Git clone/update completed successfully"
