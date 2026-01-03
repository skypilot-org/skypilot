# CLAUDE.md - SkyPilot Development Guide

This document provides guidance for AI assistants working with the SkyPilot codebase.

## Project Overview

SkyPilot is a system to run, manage, and scale AI workloads on any AI infrastructure. It provides a unified interface across 25+ cloud providers (AWS, GCP, Azure, Kubernetes, Slurm, and many others), enabling users to launch compute resources, run jobs, and serve models without vendor lock-in.

**Key capabilities:**
- Multi-cloud orchestration and resource provisioning
- Job management with automatic failover and spot instance support
- Model serving with autoscaling (SkyServe)
- Managed jobs with preemption recovery
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
uv venv --python 3.10
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
export SKYPILOT_DEBUG=1                    # Enable debug logging
export SKYPILOT_DISABLE_USAGE_COLLECTION=1 # Disable telemetry
export SKYPILOT_MINIMIZE_LOGGING=1         # Minimal output (demos)
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
- mypy==1.14.1
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

# Smoke tests (WARNING: launches ~20 cloud clusters)
pytest tests/test_smoke.py

# Smoke tests with auto-cleanup on failure
pytest tests/test_smoke.py --terminate-on-failure

# Cloud-specific tests
pytest tests/test_smoke.py --gcp
pytest tests/test_smoke.py --aws
pytest tests/test_smoke.py --azure

# Re-run failed tests
pytest --lf
```

### Test Configuration

From `pyproject.toml`:
- Uses pytest-xdist with 16 parallel workers
- Environment: `SKYPILOT_DEBUG=1`, `SKYPILOT_DISABLE_USAGE_COLLECTION=1`
- Buildkite integration for CI

### Testing in Container

```bash
docker run -it --rm berkeleyskypilot/skypilot-debug /bin/bash
# Then: uv pip install -e ".[all]"
```

### Testing with PostgreSQL Backend

SkyPilot supports PostgreSQL as an alternative to SQLite for the API server database. To run tests with PostgreSQL:

**Local PostgreSQL Setup:**

```bash
# Start a local PostgreSQL container
docker run --name skypilot-postgres -e POSTGRES_USER=skypilot \
    -e POSTGRES_PASSWORD=skypilot -e POSTGRES_DB=skypilot \
    -p 5432:5432 -d postgres:14
```

Configure SkyPilot to use PostgreSQL via config file (`~/.sky/config.yaml`):

```yaml
db: postgresql://skypilot:skypilot@localhost:5432/skypilot
```

**Running Tests with PostgreSQL:**

```bash
# Run smoke tests with PostgreSQL backend
pytest tests/smoke_tests/ --postgres

# Run with jobs consolidation mode (recommended for PostgreSQL)
pytest tests/smoke_tests/ --postgres --jobs-consolidation
```

**Production-Scale Testing:**

For large-scale performance testing with PostgreSQL, use the load test scripts:

```bash
# Run production performance test with AWS RDS PostgreSQL
bash tests/load_tests/db_scale_tests/test_large_production_performance.sh \
    --postgres --restart-api-server

# Manual data injection for debugging
python tests/load_tests/db_scale_tests/inject_production_scale_data.py \
    --active-cluster scale-test-active \
    --terminated-cluster scale-test-terminated \
    --managed-job-id 1
```

See `tests/load_tests/db_scale_tests/README.md` and `README_POSTGRES.md` for detailed instructions.

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

Example of a well-documented constraint:
```python
# uvicorn 0.36.0 removed setup_event_loop(), but we now support both
# old (setup_event_loop) and new (get_loop_factory) approaches
'uvicorn[standard] >=0.33.0',
```

## Key Modules Reference

| Module | Purpose |
|--------|---------|
| `sky/core.py` | Core orchestration (launch, exec, stop, down) |
| `sky/task.py` | Task YAML parsing and specification |
| `sky/resources.py` | Resource requirements (GPUs, memory, disk) |
| `sky/optimizer.py` | Cloud selection and cost optimization |
| `sky/client/sdk.py` | Python SDK implementation |
| `sky/client/cli/` | CLI commands |
| `sky/backends/cloud_vm_ray_backend.py` | Main execution backend |
| `sky/provision/provisioner.py` | Resource provisioning |
| `sky/jobs/controller.py` | Managed job orchestration |
| `sky/serve/` | Model serving (SkyServe) |

## Pull Request Guidelines

1. **Branch from master**, create descriptive branch name
2. **Run `format.sh`** before committing
3. **Add tests** for core system changes
4. **Run smoke tests** for significant changes
5. **Include `Tested:` section** in PR description
6. **Delete branch** after merging

### Commit Message Format

Use the `[Area] Description` format:
- `[Core]` for core system changes
- `[CLI]` for CLI changes
- `[API]` for API server changes
- `[Docs]` for documentation
- `[AWS]`, `[GCP]`, `[Azure]`, `[Kubernetes]` for cloud-specific changes
- `[Jobs]` for managed jobs
- `[Serve]` for SkyServe
- `[Dashboard]` for web UI changes

Examples:
- `[Core] Fix cluster status refresh logic`
- `[AWS] Add support for new instance types`
- `[Docs] Update installation guide`

## API Server Helm Deployment Testing

```bash
# Build local changes
helm repo add skypilot https://helm.skypilot.co
helm repo update
helm dependency build ./charts/skypilot

# Build Docker image
DOCKER_IMAGE=my-repo/skypilot:v1
docker buildx build --push --platform linux/amd64 -t $DOCKER_IMAGE -f Dockerfile .

# Deploy
NAMESPACE=skypilot
RELEASE_NAME=skypilot
helm upgrade --install $RELEASE_NAME ./charts/skypilot --devel \
    --namespace $NAMESPACE \
    --create-namespace \
    --set apiService.image=$DOCKER_IMAGE
```

## Common Pitfalls

1. **Don't modify `sky/schemas/generated/`** - These are auto-generated
2. **Match formatter versions exactly** - Version mismatches cause CI failures
3. **Test on multiple Python versions** - Support range is 3.8-3.11
4. **Consider import time** - Heavy imports slow down CLI responsiveness
5. **Handle cloud differences** - Not all features work on all clouds
6. **API versioning** - Always maintain backward compatibility

## Useful Commands

```bash
# Profile CLI performance
uv pip install py-spy
py-spy record -t -o sky.svg -- python -m sky.cli status

# Check cloud credentials
sky check

# View cluster status
sky status

# Interactive cluster access
sky exec <cluster> -- bash
```

## Documentation

- **User docs**: https://docs.skypilot.co/
- **Source**: `docs/source/` (Sphinx)
- **Build docs**: See `.readthedocs.yml`

## Getting Help

- GitHub Issues: https://github.com/skypilot-org/skypilot/issues
- Discussions: https://github.com/skypilot-org/skypilot/discussions
- Slack: http://slack.skypilot.co
