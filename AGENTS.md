# CLAUDE.md - SkyPilot Development Guide

This document provides guidance for AI assistants working with the SkyPilot codebase.

## Project Overview

SkyPilot is a system to run, manage, and scale AI workloads on any AI infrastructure. It provides a unified interface across 25+ cloud providers (AWS, GCP, Azure, Kubernetes, Slurm, and many others), enabling users to launch compute resources, run jobs, and serve models without vendor lock-in.

**Key capabilities:**
- **Clusters**: Launch and manage compute clusters across clouds and Kubernetes
- **Managed jobs**: Run jobs with automatic recovery from preemptions and failures
- **Multi-cloud and multi-Kubernetes**: Unified interface across 25+ clouds and multiple K8s clusters
- Cost optimization and GPU availability maximization

## Repository Structure

```
skypilot/
├── sky/                    # Main source code
│   ├── __init__.py         # Package exports and version
│   ├── core.py             # Core orchestration logic
│   ├── task.py             # Task definition and YAML parsing
│   ├── resources.py        # Cloud resource specifications
│   ├── optimizer.py        # Resource allocation optimizer
│   ├── execution.py        # Task execution pipeline
│   ├── exceptions.py       # Custom exceptions
│   ├── skypilot_config.py  # Configuration management
│   ├── clouds/             # Cloud provider abstractions (25+ providers)
│   ├── backends/           # Execution backends (Ray-based VM, Docker)
│   ├── provision/          # Cloud resource provisioning
│   ├── jobs/               # Managed job lifecycle
│   ├── serve/              # Model serving (SkyServe)
│   ├── skylet/             # On-cluster execution agent
│   ├── client/             # SDK and CLI
│   ├── server/             # API server and dashboard backend
│   ├── dashboard/          # Web UI (Next.js)
│   ├── utils/              # 50+ utility modules
│   ├── catalog/            # Cloud pricing and instance catalogs
│   ├── data/               # Storage and data handling
│   ├── adaptors/           # Cloud-specific metadata adapters
│   └── schemas/            # Protobuf definitions and API schemas
├── tests/                  # Test suite
│   ├── unit_tests/         # Unit tests with subdirectories per module
│   ├── smoke_tests/        # Quick validation tests
│   ├── integration_tests/  # End-to-end tests
│   ├── kubernetes/         # K8s-specific tests
│   └── conftest.py         # Pytest fixtures
├── examples/               # 50+ usage examples
├── llm/                    # 45+ LLM training/serving examples
├── docs/                   # Sphinx documentation
├── charts/                 # Helm charts for K8s deployment
└── format.sh               # Code formatting script
```

## Development Setup

### Environment Setup

```bash
# Create virtual environment with uv (Python 3.8-3.11 supported)
# --seed is required to ensure pip is installed (needed for building wheels)
uv venv --seed --python 3.11
source .venv/bin/activate

# Install in editable mode with all cloud support
uv pip install -e ".[all]"
# Or specific clouds only:
# uv pip install -e ".[aws,gcp,kubernetes]"

# Install development dependencies
uv pip install -r requirements-dev.txt

# Optional: Install pre-commit hooks
uv pip install pre-commit
pre-commit install
```

### Environment Variables

```bash
export SKYPILOT_DEV=1                      # Enable development mode
export SKYPILOT_DEBUG=1                    # Enable debug logging
```

## Code Formatting and Linting

**Always run `format.sh` before committing:**

```bash
bash format.sh         # Format changed files (vs origin/master)
bash format.sh --all   # Format entire codebase
bash format.sh --files path/to/file.py  # Format specific files
```

The script runs:
1. **Black** - IBM-specific code only (`sky/skylet/providers/ibm/`)
2. **YAPF** - Google style for all other Python code
3. **isort** - Import sorting (Google profile)
4. **mypy** - Type checking
5. **pylint** - Linting with custom rules

### Tool Versions (must match exactly)

From `requirements-dev.txt`:
- yapf==0.32.0
- pylint==2.14.5
- black==22.10.0
- mypy==1.19.1
- isort==5.12.0
- pylint-quotes==0.2.3

### Excluded from Formatting

- `sky/skylet/providers/ibm/` - Uses Black instead of YAPF
- `sky/schemas/generated/` - Auto-generated protobuf files
- `build/` - Build artifacts

## Testing

### Running Tests

```bash
# Unit tests (fast, no cloud resources)
pytest tests/unit_tests/

# Specific test file
pytest tests/unit_tests/test_resources.py
```

### CI Tests via PR Comments

Trigger CI tests on pull requests using comments:
- `/quicktest-core` - Run quick core tests
- `/smoke-test` - Run smoke tests (launches cloud clusters)
- `/smoke-test --kubernetes --postgres` - Test with PostgreSQL backend on Kubernetes
- `/smoke-test --kubernetes --remote-server --postgres` - Test remote API server with PostgreSQL

### Test Configuration

From `pyproject.toml`:
- Uses pytest-xdist with 16 parallel workers
- Environment: `SKYPILOT_DEBUG=1`, `SKYPILOT_DEV=1`
- Buildkite integration for CI

### Checking Buildkite CI Status

To check CI test results for a PR:

1. **Get Buildkite URL from GitHub**: Check the PR's commit status checks to find the Buildkite build URL (may take a moment to appear after triggering)
2. **Fetch build logs via Buildkite API**: Use the Buildkite API to retrieve detailed logs and test results

```bash
# Example: Get build details (requires BUILDKITE_TOKEN)
curl -H "Authorization: Bearer $BUILDKITE_TOKEN" \
  "https://api.buildkite.com/v2/organizations/skypilot/pipelines/skypilot/builds/<build-number>"
```

## Code Style Guidelines

### General Principles

- Follow [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
- Use f-strings over `.format()` for readability
- Use `class MyClass:` not `class MyClass(object):`
- Use `abc` module for abstract classes
- Use Python typing with `if typing.TYPE_CHECKING:` for type-only imports

### TODOs and FIXMEs

Always include author attribution:
```python
# TODO(username): Description of what needs to be done
# FIXME(username): Description of what needs fixing
```

### Exceptions vs Assertions

- Use exceptions for errors (better error messages)
- Use `assert` only for debugging/proof-checking

### Lazy Imports

For modules with significant import time (>100ms) that are imported during `import sky`, use `LazyImport` from `sky/adaptors/common.py`:

```python
from sky.adaptors.common import LazyImport
heavy_module = LazyImport('heavy_module')
```

Measure import time with:
```bash
python -X importtime -c "import sky" 2> import.log
tuna import.log  # pip install tuna
```

## Architecture Patterns

### Cloud Provider Abstraction

All cloud providers inherit from `sky.clouds.Cloud` (in `sky/clouds/cloud.py`). Cloud objects are **lightweight and stateless**. Key design principles:

- Methods should be inexpensive to call
- Don't store heavy state in cloud objects
- Cache cloud-specific queries in `sky/clouds/utils/` modules
- Each cloud implements feature flags via `CloudImplementationFeatures`

Adding a new cloud provider:
1. Create `sky/clouds/<provider>.py` inheriting from `Cloud`
2. Implement required abstract methods
3. Add provisioning logic in `sky/provision/<provider>/`
4. Register in `sky/clouds/__init__.py`

### Client-Server Architecture

SkyPilot uses a client-server model with API versioning:

- Client (SDK/CLI) in `sky/client/`
- Server in `sky/server/`
- API version in `sky/server/constants.py`

**Backward Compatibility Rules (from v0.10.0+):**
- Changes must be backward compatible
- Bump `API_VERSION` when introducing API changes
- Use `@versions.minimal_api_version(N)` decorator for new SDK methods
- Handle version differences with `versions.get_remote_api_version()`

### Protobuf Regeneration

When modifying `.proto` files in `sky/schemas/proto/`:

```bash
python -m grpc_tools.protoc \
    --proto_path=sky/schemas/generated=sky/schemas/proto \
    --python_out=. \
    --grpc_python_out=. \
    --pyi_out=. \
    sky/schemas/proto/*.proto
```

### Dependency Management

Dependencies are defined in `sky/setup_files/dependencies.py`:

- **Core dependencies**: Listed in `install_requires`
- **Cloud-specific**: Defined in `extras_require` (e.g., `aws`, `gcp`, `kubernetes`)
- **Development**: Listed in `requirements-dev.txt`

When updating dependencies:

1. Check version constraints carefully - some packages have breaking changes
2. Consider Python version compatibility (3.8-3.11)
3. Test with both minimum and latest allowed versions
4. Document version constraints with comments explaining why

## Key Modules Reference

| Module | Purpose |
|--------|---------|
| `sky/core.py` | Core orchestration (launch, exec, stop, down) |
| `sky/task.py` | Task YAML parsing and specification |
| `sky/resources.py` | Resource requirements (GPUs, memory, disk) |
| `sky/optimizer.py` | Cloud selection and cost optimization |
| `sky/client/sdk.py` | Python SDK implementation |
| `sky/client/cli/` | CLI commands |
| `sky/backends/cloud_vm_ray_backend.py` | Main execution backend for clusters |
| `sky/provision/provisioner.py` | Resource provisioning |
| `sky/jobs/` | Managed jobs with recovery and scheduling |

## Pull Request Guidelines

1. **Branch from master**, create descriptive branch name
2. **Run `format.sh`** before committing
3. **Add tests** for core system changes
4. **Run smoke tests** for significant changes
5. **Include `Tested:` section** in PR description with test plan
6. **Delete branch** after merging

### PR Description Format

PRs should include:
- **Summary**: Brief description of changes (1-3 bullet points)
- **Test plan**: How the changes were tested (commands run, manual verification steps)

**Important**: Always generate a manual test plan describing how to verify the changes work correctly. Include specific commands, expected outputs, or UI verification steps. Whenever possible, add unit tests and smoke tests for the changes.

### Commit Message Format

Use the `[Area] Description` format:
- `[Core]` for core system changes
- `[CLI]` for CLI changes
- `[API]` for API server changes
- `[Docs]` for documentation
- `[AWS]`, `[GCP]`, `[Azure]`, `[Kubernetes]` for cloud-specific changes
- `[Jobs]` for managed jobs
- `[Dashboard]` for web UI changes
- `[Serve]` for model serving (SkyServe)
- `[Test]` for testing changes
- `[CI]` for CI/CD changes

Examples:
- `[Core] Fix cluster status refresh logic`
- `[AWS] Add support for new instance types`
- `[Docs] Update installation guide`

## API Server Testing

### Local API Server (Recommended for Development)

```bash
# Always restart API server after code changes to pick up changes
sky api stop
sky api start

# Verify server is running
sky api status
```

### Dashboard Development

**For local API server development**, rebuild the dashboard before restarting:

```bash
# Install dependencies (first time or after package.json changes)
npm --prefix sky/dashboard install

# Rebuild the dashboard
npm --prefix sky/dashboard run build

# Then restart the API server
sky api stop
sky api start
```

**For remote API server (Docker/Kubernetes)**, the Dockerfile automatically builds the dashboard - no manual build needed before `docker build`.

The dashboard is a Next.js application. For development with hot reloading:

```bash
# Run dashboard in development mode (separate from API server)
cd sky/dashboard
npm run dev
```

### Mocking Remote API Server Locally

To test remote API server behavior locally:

```bash
# Start local API server (runs on port 46580 by default)
sky api stop
sky api start

# Forward to a different port to simulate remote server
socat TCP-LISTEN:46590,fork TCP:127.0.0.1:46580 &

# Connect to the forwarded port as if it were a remote server
sky api login -e http://127.0.0.1:46590
```

### Remote API Server (Kubernetes Deployment)

For testing with a remote API server on Kubernetes:

```bash
# Build local changes
helm repo add skypilot https://helm.skypilot.co
helm repo update
helm dependency build ./charts/skypilot

# Build Docker image
DOCKER_IMAGE=my-repo/skypilot:v1
docker buildx build --push --platform linux/amd64 -t $DOCKER_IMAGE -f Dockerfile .

# Deploy (NEW installation)
NAMESPACE=skypilot
RELEASE_NAME=skypilot
helm upgrade --install $RELEASE_NAME ./charts/skypilot --devel \
    --namespace $NAMESPACE \
    --create-namespace \
    --set apiService.image=$DOCKER_IMAGE
```

#### Upgrading Existing Deployments

**CRITICAL:** Always use `--reuse-values` to preserve database/credential config:

```bash
# Upgrade existing deployment (keeps PostgreSQL, auth, etc.)
helm upgrade skypilot ./charts/skypilot -n skypilot --reuse-values \
    --set apiService.image=$DOCKER_IMAGE

# Check current values / rollback if needed
helm get values skypilot -n skypilot
helm rollback skypilot <revision> -n skypilot
```

#### PostgreSQL Backend

```bash
# Create connection secret
kubectl create secret generic db-uri -n skypilot \
    --from-literal=uri="postgresql://user:pass@host:5432/db"

# Deploy with PostgreSQL
helm upgrade --install skypilot ./charts/skypilot -n skypilot \
    --set apiService.dbConnectionSecretName=db-uri \
    --set storage.enabled=false
```

## Critical Code Paths (Handle with Care)

The following modules contain complex, stateful logic that requires careful review when modifying:

**Managed Jobs Recovery:**
- `sky/jobs/controller.py` - Job lifecycle management, state transitions, async coordination
- `sky/jobs/recovery_strategy.py` - Preemption recovery, retry logic for managed jobs
- `sky/backends/cloud_vm_ray_backend.py` - Execution backend with complex state handling

These modules handle edge cases like preemption during job submission, controller failures mid-recovery, and race conditions between concurrent operations. Changes can have subtle effects on job reliability.

**API Server Performance & Robustness:**
- `sky/server/` - API server with memory efficiency, low latency requirements
- `sky/backends/backend_utils.py` - Cluster status caching, network resilience, SSH handling

These modules are optimized for performance. Be cautious about adding blocking calls, memory-heavy operations, or changing caching behavior.

**CLI/SDK Interface Design:**
- `sky/client/cli/` - Command-line interface
- `sky/client/sdk.py` - Python SDK

Be cautious about adding new interfaces or changing existing UX significantly. Keep the interface clean and minimal for both CLI and SDK.

**Backward Compatibility:**
- Test with old server + new client, and old client + new server
- Ensure existing clusters and jobs continue working after server upgrades
- `/quicktest-core` tests some backward compatibility scenarios
- See `docs/source/developers/CONTRIBUTING.md` for API versioning guidelines

## Common Pitfalls

1. **Always restart API server after code changes** - Run `sky api stop; sky api start` to pick up changes
2. **Don't modify `sky/schemas/generated/`** - These are auto-generated
3. **Match formatter versions exactly** - Version mismatches cause CI failures
4. **Consider import time** - Heavy imports slow down CLI responsiveness
5. **API versioning** - Always maintain backward compatibility

## Useful Commands

```bash
# Profile CLI performance
uv pip install py-spy
py-spy record -t -o sky.svg -- python -m sky.cli status

# Check cloud credentials
sky check

# View cluster status
sky status

# Launch a cluster
sky launch <cluster-name> <cluster-spec.yaml>

# Execute a command on the cluster
sky exec <cluster-name> -- bash

# SSH into the cluster
ssh <cluster-name>

# Launch a managed job
sky jobs launch <job-spec.yaml>
```

## Documentation

- **User docs**: https://docs.skypilot.co/
- **Source**: `docs/source/` (Sphinx)
- **Build docs**: See `.readthedocs.yml`

## Additional Resources

- **Full contributing guide**: `docs/source/developers/CONTRIBUTING.md`
- **User docs**: https://docs.skypilot.co/
- **GitHub Issues**: https://github.com/skypilot-org/skypilot/issues
- **Discussions**: https://github.com/skypilot-org/skypilot/discussions
- **Slack**: http://slack.skypilot.co
