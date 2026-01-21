# CLAUDE.md - SkyPilot Development Guide

## Project Overview

SkyPilot is a system to run, manage, and scale AI workloads on any infrastructure. It provides a unified interface across 25+ cloud providers (AWS, GCP, Azure, Kubernetes, Slurm, etc.), enabling users to launch clusters, run jobs, and serve models without vendor lock-in.

## Repository Structure

```
skypilot/
├── sky/          # Main source code
├── tests/        # Unit tests, smoke tests (e2e), integration tests
├── examples/     # Usage examples
├── llm/          # LLM training/serving examples
├── docs/         # Sphinx documentation
├── charts/       # Helm charts for K8s deployment
└── format.sh     # Code formatting script
```

## Development

Activate the virtual environment: `source .venv/bin/activate`

For initial setup, see `docs/source/developers/CONTRIBUTING.md`.

### Environment Variables

```bash
export SKYPILOT_DEV=1      # Enable development mode
export SKYPILOT_DEBUG=1    # Enable debug logging
```

### Code Formatting

**Always run before committing:**

```bash
bash format.sh         # Format changed files (vs origin/master)
bash format.sh --all   # Format entire codebase
```

Tool versions must match `requirements-dev.txt` exactly or CI will fail.

### Testing

```bash
pytest tests/unit_tests/                    # Run all unit tests
pytest tests/unit_tests/test_resources.py   # Specific file
```

**CI triggers via PR comments:**
- `/quicktest-core` - Quick core tests
- `/smoke-test` - Smoke tests / e2e tests (launches real cloud clusters)

**Checking Buildkite CI status:**
```bash
# Get build details (requires BUILDKITE_TOKEN)
curl -H "Authorization: Bearer $BUILDKITE_TOKEN" \
  "https://api.buildkite.com/v2/organizations/skypilot/pipelines/skypilot/builds/<build-number>"
```

For smoke test options, see CONTRIBUTING.md "Testing" section.

## Code Style

Follow [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).

**Project conventions:**
- Use `TODO(username):` / `FIXME(username):` with attribution
- Use exceptions for errors (better messages). Use `assert` for impossible code paths / type checker hints.
- Use f-strings over `.format()` for readability
- Use `class MyClass:` not `class MyClass(object):`
- Use `if typing.TYPE_CHECKING:` for type-only imports

**Lazy imports:** For modules with significant import time (>100ms) during `import sky`, use `LazyImport`:
```python
from sky.adaptors.common import LazyImport
heavy_module = LazyImport('heavy_module')
```

## Architecture

### Cloud Providers

All clouds inherit from `sky.clouds.Cloud` (in `sky/clouds/cloud.py`). Cloud objects are **lightweight and stateless**:
- Methods should be inexpensive to call
- Don't store heavy state in cloud objects
- Cache cloud-specific queries in `sky/clouds/utils/` modules
- Each cloud implements feature flags via `CloudImplementationFeatures`

For adding a new cloud provider, see [Guide: Adding a New Cloud](https://docs.google.com/document/d/1oWox3qb3Kz3wXXSGg9ZJWwijoa99a3PIQUHBR8UgEGs).

### Client-Server

- Client (SDK/CLI): `sky/client/`
- Server: `sky/server/`
- API version: `sky/server/constants.py`

Bump `API_VERSION` when introducing API changes. For detailed backward compatibility rules, see CONTRIBUTING.md "Backward compatibility guidelines".

### Protobuf Regeneration

When modifying `.proto` files in `sky/schemas/proto/`:

```bash
python -m grpc_tools.protoc \
    --proto_path=sky/schemas/generated=sky/schemas/proto \
    --python_out=. --grpc_python_out=. --pyi_out=. \
    sky/schemas/proto/*.proto
```

## Key Modules

| Module | Purpose |
|--------|---------|
| `sky/core.py` | Core orchestration (launch, exec, stop, down) |
| `sky/client/sdk.py` | Python SDK |
| `sky/client/cli/` | CLI commands |
| `sky/backends/cloud_vm_ray_backend.py` | Main execution backend |
| `sky/jobs/` | Managed jobs with recovery |

## API Server

```bash
# Always restart after code changes
sky api stop && sky api start
sky api status  # Verify running
```

For mocking remote API server locally (socat trick) and Helm deployment, see CONTRIBUTING.md "Testing the API server".

## Critical Code Paths

Handle these modules with care—they contain complex, stateful logic:

**Managed Jobs Recovery:**
- `sky/jobs/controller.py` - Job lifecycle, state transitions, async coordination
- `sky/jobs/recovery_strategy.py` - Preemption recovery, retry logic
- `sky/backends/cloud_vm_ray_backend.py` - Execution backend with complex state

**API Server Performance:**
- `sky/server/` - Memory efficiency, low latency requirements
- `sky/backends/backend_utils.py` - Cluster status caching, SSH handling

These modules are optimized for performance. Be cautious about adding blocking calls, memory-heavy operations, or changing caching behavior.

**CLI/SDK Interface:**
- `sky/client/cli/` and `sky/client/sdk.py` - Keep interface clean and minimal
- Changes must be backward compatible (e.g., new arguments must be optional)

## Common Pitfalls

1. **Restart API server after code changes** - `sky api stop; sky api start`
2. **Don't modify `sky/schemas/generated/`** - Auto-generated files
3. **Match formatter versions exactly** - Version mismatches cause CI failures
4. **Consider import time** - Heavy imports slow CLI responsiveness
5. **Maintain backward compatibility** - See CONTRIBUTING.md for API versioning

## Pull Requests

**PR description format:**
- Summary: 1-3 bullet points
- Test plan: Commands run, verification steps

**Commit message format:** `[Area] Description`
- Areas: `[Core]`, `[CLI]`, `[API]`, `[AWS]`, `[GCP]`, `[Azure]`, `[Kubernetes]`, `[Jobs]`, `[Dashboard]`, `[Serve]`, `[Docs]`, `[Test]`, `[CI]`

## Useful Commands

```bash
sky check                      # Check cloud credentials
sky status                     # View cluster status
sky exec <cluster> -- <cmd>    # Run command on cluster
ssh <cluster>                  # SSH into cluster

# Profile CLI performance
py-spy record -t -o sky.svg -- python -m sky.cli status
```

## Resources

- **User docs**: https://docs.skypilot.co/
- **Contributing guide**: `docs/source/developers/CONTRIBUTING.md`
- **Issues**: https://github.com/skypilot-org/skypilot/issues
