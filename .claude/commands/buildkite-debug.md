# Buildkite Debug Command

Debug Buildkite CI test failures by analyzing logs, SSHing into the environment if available, and using sky commands to diagnose issues.

## Usage

```
/buildkite-debug <buildkite-url-or-log-file> [test_name]
```

## Arguments

- `buildkite-url-or-log-file`: Either:
  - A Buildkite build URL: `https://buildkite.com/skypilot-1/smoke-tests/builds/7446`
  - A Buildkite job URL with fragment: `https://buildkite.com/skypilot-1/smoke-tests/builds/7446#019bd4e2-0ae3-4c51-8823-044232d71fb2`
  - A local log file path: `@1.log` or `/path/to/build.log`
- `test_name` (optional): Specific test to debug (e.g., `test_kubernetes_context_failover`). If not provided, debug all failures.

## Instructions

When the user invokes this command, follow these steps:

### Step 1: Parse Input and Fetch Build Information

**If a Buildkite URL is provided:**
1. Parse the URL to extract org_slug, pipeline_slug, build_number, and optional job_id from fragment
2. Use `mcp__buildkite__get_build` to fetch build details with `job_state: "failed"` filter
3. For each failed job, use `mcp__buildkite__tail_logs` to get the last 200 lines of logs

**If a log file path is provided (with @ prefix or absolute path):**
1. Read the log file using the Read tool
2. Parse the build information from the log content

### Step 2: Extract Environment Information

From the logs, look for the "Preparing working directory" section near the top:
```
~~~ Preparing working directory
$ cd /home/buildkite/.buildkite-agent/builds/kind/54-89-146-255-buildkite-kind1-1/skypilot-1/smoke-tests
```

Extract:

1. **IP Address**: From the path segment like `54-89-146-255-buildkite-kind1-1`
   - Convert dash-separated IP to dot notation (e.g., `54-89-146-255` → `54.89.146.255`)

2. **Container Name**: From the path segment (e.g., `buildkite-kind1` from `54-89-146-255-buildkite-kind1-1`)
   - The format is `<IP>-<container-name>-<instance-number>`

3. **Working Directory**: The full path from the `cd` command (e.g., `/home/buildkite/.buildkite-agent/builds/kind/54-89-146-255-buildkite-kind1-1/skypilot-1/smoke-tests`)

4. **Git Commit**: From `git checkout -f <commit-sha>` lines

5. **Branch Name**: From `git fetch -v --prune -- origin <branch>` lines

6. **AWS Region**: Default to `us-east-1`

### Step 2.5: Sync Local Codebase to Buildkite Branch

**IMPORTANT**: Before analyzing code, sync the local codebase to match the Buildkite build for accurate debugging.

1. **Check current local branch and commit:**
```bash
git branch --show-current
git rev-parse HEAD
```

2. **Compare with Buildkite branch/commit:**
   - If the local commit matches the Buildkite commit, no action needed
   - If they differ, proceed to sync

3. **Sync to Buildkite branch and commit:**
```bash
# Stash any local changes first
git stash push -m "buildkite-debug: stashing before sync"

# Fetch the latest from origin
git fetch origin

# Checkout the Buildkite branch
git checkout <branch-name>

# Reset to the exact commit from Buildkite
git checkout <commit-sha>
```

4. **Notify user of the sync:**
> **INFO**: Synced local codebase to match Buildkite build.
> - Branch: `<branch-name>`
> - Commit: `<commit-sha>`
> - Previous branch: `<previous-branch>`
> - Stashed changes: `<yes/no>`

5. **If sync fails**, warn the user and continue with current codebase:
> **WARNING**: Could not sync to Buildkite branch/commit. Analyzing with current codebase which may differ from CI.

### Step 3: Identify Failures

Search logs in this order:
1. `Reason:` - The smoke test framework outputs `Reason: <command>` when a test fails (see `smoke_tests_utils.py`)
2. `FAILED` - pytest test failures
3. `Exception:` - Python exceptions
4. `Error:` - General errors

For each failure, extract:
- Test name
- Error message
- Stack trace (if available)
- The command that failed (from the `Reason:` line)

If `test_name` argument was provided, filter to only that test.

### Step 4: Analyze Failures

For each failure:
1. Identify what the test was trying to do
2. Determine the root cause from logs
3. Check if it's a known flaky test or infrastructure issue
4. Compare with current codebase to see if it's a code issue

### Step 5: Attempt SSH Access

1. Check if the instance is still running:
```bash
aws ec2 describe-instances --region us-east-1 --filters "Name=ip-address,Values=<IP>" --query "Reservations[*].Instances[*].[InstanceId,State.Name]" --output text
```

2. **If instance is running:**
```bash
INSTANCE_ID=$(aws ec2 describe-instances --region us-east-1 --filters "Name=ip-address,Values=<IP>" --query "Reservations[*].Instances[*].InstanceId" --output text)
aws ec2-instance-connect ssh --instance-id $INSTANCE_ID --region us-east-1 --os-user ubuntu
```

3. **If instance is shutting-down/terminated:**
   - Warn user: "The Buildkite agent at <IP> is no longer available (state: <state>)"
   - Continue with code analysis only

### Step 6: Interactive Debugging (if SSH available)

Once SSHed in:

1. **Find and attach to container, then cd to working directory:**
```bash
docker ps | grep buildkite
# Use the container name extracted from Step 2 (e.g., buildkite-kind1)
docker exec -it <container-name> /bin/bash
# Inside container: cd to the working directory extracted from Step 2
cd <working-directory>
```

**Note**: Inside the container, the kind Kubernetes cluster (or any k8s cluster configured for CI) is accessible via `kubectl`. You can use `kubectl` commands to inspect pods, logs, and cluster state:
```bash
kubectl get pods -A
kubectl get nodes
kubectl logs <pod-name> -n <namespace>
```

2. **Verify git commit (CRITICAL):**
```bash
git rev-parse HEAD
# Compare with expected: <commit-from-logs>
```

If commits don't match, warn:
> **WARNING**: Git commit mismatch! Current: `<actual>`, Expected: `<expected>`. Buildkite may have started a new job.

3. **Debug with sky commands:**
```bash
# Check cluster status
sky status

# Check managed jobs
sky jobs queue

# View specific cluster logs
sky logs <cluster-name>

# Check API server
sky api status
sky api logs

# Re-run the failing test (LOG_TO_STDOUT=1 outputs logs to terminal)
LOG_TO_STDOUT=1 pytest <test-file>::<test-name> -v
```

### Step 7: Code Analysis (when SSH not available)

If the environment is not accessible, analyze the code:

1. Search for the failing test in the codebase
2. Understand what the test is checking
3. Look at recent changes to related code
4. Identify potential causes of failure
5. Suggest fixes or next steps

### Output Format

Provide a structured report:

```
## Build Information
- **Build URL**: <url>
- **Branch**: <branch>
- **Commit**: <commit>
- **Status**: <passed/failed>

## Environment
- **IP Address**: <ip>
- **Container**: <container>
- **Working Dir**: <path>
- **SSH Status**: <available/unavailable (reason)>

## Local Codebase Sync
- **Synced**: <yes/no>
- **Branch**: <branch-name>
- **Commit**: <commit-sha>
- **Previous Branch**: <previous-branch> (if synced)

## Failures Found
### 1. <test_name>
- **Failed Command**: `<command>`
- **Error**: <error message>
- **Root Cause**: <analysis>
- **Suggested Fix**: <suggestion>

## Debug Session (if SSH available)
<interactive debug output>

## Recommendations
1. <recommendation 1>
2. <recommendation 2>
```

## Examples

### Debug all failures in a build:
```
/buildkite-debug https://buildkite.com/skypilot-1/smoke-tests/builds/7446
```

### Debug a specific job:
```
/buildkite-debug https://buildkite.com/skypilot-1/smoke-tests/builds/7446#019bd4e2-0ae3-4c51-8823-044232d71fb2
```

### Debug a specific test:
```
/buildkite-debug https://buildkite.com/skypilot-1/smoke-tests/builds/7446 test_kubernetes_context_failover
```

### Debug from a log file:
```
/buildkite-debug @1.log
/buildkite-debug /path/to/build.log test_basic
```
