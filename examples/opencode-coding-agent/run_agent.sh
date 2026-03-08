#!/usr/bin/env bash
set -euo pipefail

: "${GITHUB_TOKEN:?}"
: "${GITHUB_REPO:?}"
: "${ANTHROPIC_API_KEY:?}"
: "${AGENT_TASK:?AGENT_TASK must be set}"

LLM_MODEL_ID="${LLM_MODEL_ID:-anthropic/claude-sonnet-4-6}"
WORKSPACE="/tmp/opencode-workspace"

# 1. Clone
rm -rf "$WORKSPACE"

if [[ -n "${USE_BRANCH:-}" ]]; then
  BRANCH="opencode/${USE_BRANCH}"
  git clone --depth=1 --branch "$BRANCH" \
    -c "http.https://github.com/.extraheader=Authorization: Basic $(echo -n "x-access-token:${GITHUB_TOKEN}" | base64)" \
    "https://github.com/${GITHUB_REPO}.git" "$WORKSPACE"
  cd "$WORKSPACE"
else
  BRANCH="opencode/${BRANCH_NAME:-agent}"
  git clone --depth=1 \
    "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git" "$WORKSPACE"
  cd "$WORKSPACE"
  git checkout -b "$BRANCH"
fi

echo "==> Task: $AGENT_TASK"

# 2. Run agent
opencode run "$AGENT_TASK" --model "$LLM_MODEL_ID"

# 3. Commit and push
if [[ -z "$(git status --porcelain)" ]]; then
  echo "No changes made by the agent."
  exit 0
fi

git add -A

if ! git config user.name &>/dev/null || ! git config user.email &>/dev/null; then
  _GH_USER=$(curl -fsSL -H "Authorization: Bearer ${GITHUB_TOKEN}" https://api.github.com/user)
  git config user.name "$(echo "$_GH_USER" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('name') or d['login'])")"
  git config user.email "$(echo "$_GH_USER" | python3 -c "import sys,json; print(json.load(sys.stdin)['email'])")"
fi

opencode run \
  "Create a single git commit for the already-staged changes with an informative commit message." \
  --model "$LLM_MODEL_ID"
git push origin "$BRANCH"

BASE_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || echo "main")
echo ""
echo "Branch pushed: ${BRANCH}"
echo "Open a PR:     https://github.com/${GITHUB_REPO}/compare/${BASE_BRANCH}...${BRANCH}?expand=1"
