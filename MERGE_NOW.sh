#!/bin/bash
# Quick Merge Script - SkyPilot Rust Migration

set -e

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                                                              ║"
echo "║     🚀 SKYPILOT RUST MIGRATION - MERGE SCRIPT               ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}Branch:${NC} cursor/migrate-python-utilities-to-rust-b24c"
echo -e "${BLUE}Version:${NC} 1.0.0"
echo ""

# 1. Final checks
echo -e "${YELLOW}Step 1/5:${NC} Running final checks..."
if command -v cargo &> /dev/null; then
    cd rust && cargo build --release --quiet && cd ..
    echo -e "${GREEN}✓${NC} Rust build successful"
else
    echo -e "${YELLOW}⚠${NC} Rust not found, skipping build check"
fi

# 2. Stage all files
echo ""
echo -e "${YELLOW}Step 2/5:${NC} Staging all changes..."
git add -A
CHANGED=$(git status --short | wc -l)
echo -e "${GREEN}✓${NC} Staged $CHANGED files"

# 3. Show what will be committed
echo ""
echo -e "${YELLOW}Step 3/5:${NC} Summary of changes:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
git status --short | head -20
if [ $(git status --short | wc -l) -gt 20 ]; then
    echo "... and $(( $(git status --short | wc -l) - 20 )) more files"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 4. Confirm
echo -e "${YELLOW}Step 4/5:${NC} Ready to commit"
echo ""
echo "This will:"
echo "  • Commit $CHANGED files"
echo "  • Use message from COMMIT_MESSAGE.txt"
echo "  • Push to origin/cursor/migrate-python-utilities-to-rust-b24c"
echo ""
read -p "Proceed? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo ""
    echo "Aborted. No changes made."
    exit 0
fi

# 5. Commit and push
echo ""
echo -e "${YELLOW}Step 5/5:${NC} Committing and pushing..."

if [ -f "COMMIT_MESSAGE.txt" ]; then
    git commit -F COMMIT_MESSAGE.txt
    echo -e "${GREEN}✓${NC} Committed with prepared message"
else
    echo "Error: COMMIT_MESSAGE.txt not found"
    exit 1
fi

git push origin cursor/migrate-python-utilities-to-rust-b24c
echo -e "${GREEN}✓${NC} Pushed to remote"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                                                              ║"
echo "║     ✅ SUCCESSFULLY PUSHED TO BRANCH                        ║"
echo "║                                                              ║"
echo "║     Next: Create Pull Request on GitHub                     ║"
echo "║                                                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "PR Description: Use RELEASE_NOTES_v1.0.md"
echo "Reviewers: Tag @tech-lead @security @qa"
echo ""
echo "After approval, merge to main! 🎉"

