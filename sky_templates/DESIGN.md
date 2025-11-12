# SkyPilot Templates Design Document

**Last Updated:** 2025-11-12
**Status:** Implemented (Bundled Package Approach)

## Overview

This document captures the design decisions, rationale, and future migration paths for the `sky_templates` package in SkyPilot.

---

## Problem Statement

SkyPilot needs to deliver runtime scripts (currently Ray cluster management scripts) to remote instances. The original location (`sky/utils/sky_scripts/`) had several issues:

1. **Location confusion:** Inside the `sky` package but not actually client-side code
2. **Delivery mechanism:** Used S3/cloud storage mounting (complex, requires internet)
3. **Unclear purpose:** Mixed client utilities with remote runtime scripts

---

## Solution: `sky_templates` Package

### Migration Summary

**From:**
```
sky/
└── utils/
    └── sky_scripts/
        ├── start_ray_cluster.sh
        └── stop_ray_cluster.sh
```

**To:**
```
sky_templates/              # Outside sky/ package
├── __init__.py
├── README.md
├── DESIGN.md              # This file
└── ray/
    ├── __init__.py
    ├── start_ray_cluster.sh
    └── stop_ray_cluster.sh
```

### Delivery Mechanism

**Old approach:**
1. Scripts stored in cloud storage (S3/GCS/Azure)
2. Mounted to remote instances via `mount_skypilot_scripts()`
3. Required internet, complex setup, version mismatch risks

**New approach:**
1. Scripts bundled in SkyPilot wheel via `MANIFEST.in`
2. Installed to client's `site-packages/sky_templates/`
3. Copied to remote instances via rsync during provisioning
4. Delivered to `~/skypilot_templates/` on remote instances
5. Backwards compat symlink: `~/skypilot_scripts → ~/skypilot_templates/ray`

### Key Benefits

✅ **Simple:** No S3/cloud storage dependency
✅ **Offline-capable:** Works without internet after initial `pip install`
✅ **Version-consistent:** Templates always match SkyPilot version
✅ **Extensible:** Easy to add Python scripts, other frameworks
✅ **Backwards compatible:** Symlink ensures old YAMLs work

---

## Design Decision: Bundled vs. Separate Package

### Question Considered

Should templates be:
- **Option A:** Separate package (`pip install skypilot-templates`) like Airflow providers?
- **Option B:** Bundled with SkyPilot (`pip install skypilot` includes templates)?

### Decision: **Option B (Bundled)** ✅

**Rationale:**

| Factor | Airflow Providers | SkyPilot Templates |
|--------|------------------|-------------------|
| **Size per package** | 20-50 MB | 8.5 KB (< 0.01 MB) |
| **Total if bundled** | 500+ MB | < 0.1 MB (even with growth) |
| **Dependencies** | Heavy (boto3, google-cloud-*, etc.) | None |
| **User needs** | Most users need 2-3 providers | All users need templates |
| **Versioning** | Independent useful | Tightly coupled to provisioning |

**Conclusion:** Templates are tiny, dependency-free, and core functionality → Bundling is simpler and better UX.

### Trade-offs Accepted

**Pros of bundled approach:**
- ✅ Zero friction onboarding: `pip install skypilot` just works
- ✅ No version compatibility matrix
- ✅ Simpler provisioning code (no optional imports)
- ✅ Better documentation (no extras to explain)
- ✅ Negligible size impact (< 100 KB)

**Cons of bundled approach:**
- ❌ Users get files they might not use (but: 100 KB is negligible)
- ❌ No independent template versioning (but: templates are coupled to provisioning anyway)
- ❌ Can't easily customize templates (but: users can override via `file_mounts` or env vars)

---

## Future Migration Path: Bundled → Separate

### When to Reconsider Separation

Only if **any** of these become true:
- ❌ Templates grow to **> 10 MB** (unlikely for scripts)
- ❌ Templates add **heavy dependencies** (e.g., TensorFlow, PyTorch)
- ❌ Most users **don't need** templates (not the case)
- ❌ Need **rapid template iteration** independent of SkyPilot releases

### Migration Difficulty: **Low-Medium**

**Effort estimate:** ~18 hours technical work + 2-3 months transition period

**What would change:**

1. **Create separate package** (~2 hours)
   ```bash
   skypilot-templates/
   ├── setup.py
   └── sky_templates/
   ```

2. **Make import optional** (~2 hours)
   ```python
   try:
       import sky_templates
   except ImportError:
       logger.warning("Templates not installed...")
       return
   ```

3. **Update setup.py** (~1 hour)
   ```python
   extras_require={'templates': ['skypilot-templates>=1.0.0']}
   ```

4. **Documentation + migration guide** (~4 hours)

5. **Backwards compatibility layer** (~3 hours)
   ```python
   # Support both bundled and separate
   try:
       import sky_templates
   except ImportError:
       from sky import sky_templates  # Old bundled location
   ```

### Why Migration Stays Easy

The current design **already supports** future separation:

✅ **Separate package structure:** `sky_templates/` is isolated
✅ **Single import location:** Only `_copy_templates_to_remote()` imports it
✅ **No tight coupling:** Templates don't import from `sky`, `sky` doesn't deeply depend on templates
✅ **Clean interface:** Communication via rsync, not Python APIs

**We didn't paint ourselves into a corner.** The architecture supports both approaches.

---

## Implementation Details

### Files Changed (2025-11-12 Migration)

**Created:**
- `sky_templates/__init__.py` - Package marker with constants
- `sky_templates/README.md` - User documentation
- `sky_templates/ray/__init__.py` - Ray subpackage
- `sky_templates/ray/start_ray_cluster.sh` - Moved from old location
- `sky_templates/ray/stop_ray_cluster.sh` - Moved from old location

**Modified:**
- `MANIFEST.in` - Added `recursive-include sky_templates *.sh *.py`
- `sky/provision/instance_setup.py` - Added `copy_templates_to_remote()` function
- `sky/provision/provisioner.py` - Call `copy_templates_to_remote()` during setup
- 10 YAML examples - Updated paths from `~/skypilot_scripts/` to `~/skypilot_templates/ray/`

**Deleted:**
- `sky/utils/sky_scripts/` - Old directory (now empty and removed)

### Provisioning Flow

```
┌─────────────────────────────────────────┐
│ User's Machine                          │
│                                         │
│  pip install skypilot                  │
│  → Installs sky/ and sky_templates/    │
│                                         │
│  sky launch cluster.yaml               │
│  → Provisions instances                │
│  → Calls copy_templates_to_remote()    │
│    ├─ Reads from site-packages/        │
│    └─ Rsyncs to remote instances       │
└─────────────────────────────────────────┘
                  │
                  │ rsync
                  ▼
┌─────────────────────────────────────────┐
│ Remote Instance                         │
│                                         │
│  ~/skypilot_templates/                 │
│  ├── ray/                              │
│  │   ├── start_ray_cluster.sh         │
│  │   └── stop_ray_cluster.sh          │
│  └── ...                               │
│                                         │
│  ~/skypilot_scripts/ → (symlink)       │
│      ~/skypilot_templates/ray          │
└─────────────────────────────────────────┘
```

### Backwards Compatibility

**Symlink ensures old paths work:**
```bash
# Old YAMLs continue to work:
~/skypilot_scripts/start_ray_cluster.sh

# Points to:
~/skypilot_templates/ray/start_ray_cluster.sh
```

**Conditional checks in YAMLs:**
```yaml
if [ -f ~/skypilot_templates/ray/start_ray_cluster.sh ]; then
  ~/skypilot_templates/ray/start_ray_cluster.sh
else
  # Fallback to manual setup
fi
```

---

## Extensibility Examples

### Adding Python Scripts (Future)

**Structure:**
```
sky_templates/
├── ray/
│   ├── start_ray_cluster.sh
│   ├── cluster_manager.py        # NEW
│   └── health_check.py           # NEW
├── spark/                         # NEW
│   ├── setup_spark.sh
│   └── spark_utils.py
└── utils/                         # NEW
    └── common.py
```

**No changes needed:**
- MANIFEST.in already includes `*.py`
- Rsync copies entire directory tree
- Scripts available on both client and remote

**Usage on remote:**
```bash
python ~/skypilot_templates/ray/cluster_manager.py
python ~/skypilot_templates/spark/spark_utils.py
```

**Importable if PYTHONPATH set:**
```python
# Add to PYTHONPATH in setup:
export PYTHONPATH=$PYTHONPATH:~/skypilot_templates

# Then can import:
from ray.cluster_manager import RayCluster
from utils.common import setup_logging
```

### Adding New Frameworks

```
sky_templates/
├── ray/          # Existing
├── spark/        # Future
├── dask/         # Future
├── horovod/      # Future
└── kubernetes/   # Future
```

Each framework gets its own subdirectory with scripts and utilities.

---

## Design Principles

1. **Simplicity First:** Choose the simplest solution that works (bundled vs. separate)
2. **User Experience:** Optimize for 95% use case (zero-config installation)
3. **Fail Gracefully:** Backwards compatibility via symlinks and conditional checks
4. **Future-Proof:** Architecture supports migration if needs change
5. **Minimal Dependencies:** Keep templates dependency-free
6. **Version Consistency:** Templates match SkyPilot version

---

## Open Questions / Future Considerations

### Environment Variable Override

Allow power users to provide custom templates:
```bash
export SKYPILOT_TEMPLATES_PATH=/custom/templates
sky launch cluster.yaml
```

**Pros:** Flexibility for advanced users
**Cons:** More testing, documentation
**Decision:** Defer until requested

### Template Versioning

If templates become independently versioned:
```yaml
# In sky_templates/__init__.py
__version__ = '1.2.0'

# Compatibility check in provisioning:
if not is_compatible(sky_templates.__version__, sky.__version__):
    logger.warning("Template version mismatch...")
```

**Pros:** Can update templates independently
**Cons:** Version matrix complexity
**Decision:** Defer until separate package needed

### Template Registry

Central registry for community templates:
```bash
sky templates install apache/spark
sky templates install nvidia/nccl-tests
```

**Pros:** Ecosystem growth, community contributions
**Cons:** Significant complexity, hosting, security
**Decision:** Defer until ecosystem demand

---

## References

- **Original PR:** [Migration from sky/utils/sky_scripts/ to sky_templates/]
- **Airflow Providers:** https://airflow.apache.org/docs/apache-airflow-providers/
- **Migration Discussion:** See conversation 2025-11-12 (this file)

---

## Appendix: Size Comparison

### Current State (2025-11-12)

```bash
$ du -sh sky_templates/
12K     sky_templates/

$ ls -lh sky_templates/ray/*.sh
-rwxr-xr-x  5.4K  start_ray_cluster.sh
-rwxr-xr-x  2.9K  stop_ray_cluster.sh
```

### Projected Growth (Conservative)

| Scenario | Size | Still Bundled? |
|----------|------|----------------|
| +10 Python scripts | ~50 KB | ✅ Yes |
| +5 frameworks (Spark, Dask, etc.) | ~200 KB | ✅ Yes |
| +Large config files | ~500 KB | ✅ Yes |
| +Binary dependencies | 10+ MB | ❌ Consider separation |

**Rule of thumb:** If templates + deps > 10 MB or add heavy dependencies, reconsider separation.

---

## Summary

**Decision:** Bundled package approach (Option B)
**Rationale:** Simple UX, tiny size, zero dependencies, core functionality
**Migration Path:** Easy upgrade to separate package if needed (~18 hours)
**Status:** Implemented and shipping with SkyPilot

**Bottom line:** The current design optimizes for simplicity and user experience while keeping future migration paths open. Templates are a core, lightweight feature that benefits from being bundled with the main package.

---

## Appendix A: Custom Template Registration (Future)

If/when templates become unbundled, here's the planned discovery system for custom templates.

### Multi-Tier Discovery System

Templates discovered from multiple sources with clear priority:

```
Priority (highest to lowest):
1. Environment variable (SKYPILOT_TEMPLATES_PATH)
2. User config file (~/.sky/config.yaml)
3. Installed Python packages (entry points)
4. Official templates (skypilot-templates package)
```

### Method 1: Environment Variable (Quick Testing)

**Use case:** Individual developers testing custom scripts

```bash
# Point to local directory
export SKYPILOT_TEMPLATES_PATH=/path/to/my/templates
sky launch cluster.yaml

# Or inline
SKYPILOT_TEMPLATES_PATH=./custom_templates sky launch cluster.yaml
```

**Template structure:**
```
/path/to/my/templates/
├── ray/
│   └── my_custom_start.sh
└── spark/
    └── setup_spark.sh
```

**Pros:** ✅ Zero config, instant testing, great for dev
**Cons:** ❌ Temporary, lost when shell closes

### Method 2: Config File (Persistent Sources)

**Use case:** Teams sharing templates, multiple template sources

```yaml
# ~/.sky/config.yaml
templates:
  sources:
    # Local directory
    - path: /shared/company-templates
      priority: 100

    # Git repository (auto-cloned)
    - git: https://github.com/mycompany/sky-templates
      branch: main
      priority: 90

    # Private PyPI
    - pypi: skypilot-templates-internal
      index: https://pypi.company.com
      priority: 80

    # Official templates (lowest priority)
    - pypi: skypilot-templates
      priority: 10

  cache_dir: ~/.sky/templates_cache
```

**CLI to manage:**
```bash
# Add a source
sky templates add /path/to/templates
sky templates add git+https://github.com/user/templates

# List sources
sky templates list

# Update all git sources
sky templates update
```

**Pros:** ✅ Persistent, multiple sources, team-friendly
**Cons:** ❌ Requires sync/update mechanism

### Method 3: Python Package Entry Points (Recommended)

**Use case:** Framework maintainers publishing templates

**This follows the Airflow provider pattern** (see Appendix B for details).

**Package structure:**
```python
skypilot-templates-spark/
├── setup.py
├── pyproject.toml
└── skypilot_templates_spark/
    ├── __init__.py
    └── spark/
        ├── setup_spark.sh
        ├── configure_spark.py
        └── health_check.sh
```

**setup.py with entry point:**
```python
from setuptools import setup, find_packages

setup(
    name='skypilot-templates-spark',
    version='1.0.0',
    packages=find_packages(),
    include_package_data=True,

    # Register with SkyPilot via entry points
    entry_points={
        'skypilot.templates': [
            'spark = skypilot_templates_spark',
        ],
    },
)
```

**User experience:**
```bash
# Install template package
pip install skypilot-templates-spark

# Immediately use (auto-discovered)
sky launch spark_cluster.yaml
```

**In YAML:**
```yaml
run: |
  ~/skypilot_templates/spark/setup_spark.sh
```

**Pros:** ✅ Standard Python ecosystem, versioned, auto-discovered
**Cons:** ❌ Requires packaging knowledge

### Template Discovery Implementation

```python
# In sky/provision/instance_setup.py

def discover_template_sources():
    """Discover all registered template sources."""
    sources = []

    # 1. Environment variable (highest priority)
    if env_path := os.getenv('SKYPILOT_TEMPLATES_PATH'):
        sources.append({'type': 'env', 'path': env_path, 'priority': 1000})

    # 2. Config file sources
    config_sources = skypilot_config.get_nested(('templates', 'sources'), [])
    sources.extend(config_sources)

    # 3. Entry points (installed packages)
    try:
        import importlib.metadata as metadata
        for entry_point in metadata.entry_points().select(group='skypilot.templates'):
            module = entry_point.load()
            sources.append({
                'type': 'entry_point',
                'name': entry_point.name,
                'path': os.path.dirname(module.__file__),
                'priority': 50,
            })
    except ImportError:
        pass

    # 4. Official bundled templates (lowest priority)
    try:
        import sky_templates
        sources.append({
            'type': 'official',
            'path': os.path.dirname(sky_templates.__file__),
            'priority': 1,
        })
    except ImportError:
        pass

    # Sort by priority (highest first)
    return sorted(sources, key=lambda s: s['priority'], reverse=True)
```

### Ecosystem Example (Future)

```bash
# Core
pip install skypilot

# Official templates
pip install skypilot-templates              # Core: Ray, basic utilities
pip install skypilot-templates-spark        # Apache Spark
pip install skypilot-templates-dask         # Dask

# Framework-maintained
pip install skypilot-templates-mlflow       # By MLflow team
pip install skypilot-templates-ray-llm      # By Ray team

# Community templates
pip install skypilot-templates-megatron     # By NVIDIA
pip install skypilot-templates-company      # Private company templates
```

### Registration Summary

| Method | Use Case | Command | Persistence |
|--------|----------|---------|-------------|
| **Env Var** | Quick testing | `export SKYPILOT_TEMPLATES_PATH=...` | Session |
| **Config File** | Team sharing | `sky templates add git+...` | Permanent |
| **Entry Points** | Distribution | `pip install skypilot-templates-spark` | Permanent |

**Recommendation:** Support all methods with clear priority order for maximum flexibility.

---

## Appendix B: Entry Points Deep Dive

**Entry points are Python's standard plugin discovery mechanism**, used by pytest, Flask, Airflow, and many others.

### How Entry Points Work

Entry points allow packages to advertise "hooks" that other packages can discover at runtime.

**The discovery code:**
```python
import importlib.metadata as metadata
import os

for entry_point in metadata.entry_points().select(group='skypilot.templates'):
    module = entry_point.load()
    sources.append({
        'type': 'entry_point',
        'name': entry_point.name,
        'path': os.path.dirname(module.__file__),
        'priority': 50,
    })
```

### What This Code Does

**Line by line:**

1. **`import importlib.metadata as metadata`**
   - Python 3.8+ standard library for reading package metadata
   - Reads metadata files installed by pip/setuptools

2. **`metadata.entry_points()`**
   - Scans **all installed packages** in `site-packages/`
   - Returns ALL entry points from ALL packages

3. **`.select(group='skypilot.templates')`**
   - Filters to only entry points in the `'skypilot.templates'` group
   - Ignores other groups (e.g., `console_scripts`, `pytest11`)

4. **`entry_point.load()`**
   - Imports the module specified in the entry point
   - Returns the module object

5. **`os.path.dirname(module.__file__)`**
   - Gets the directory where the module is installed
   - This is where the template files live

### Where It Looks

**Scans these directories:**
```python
>>> import sys
>>> sys.path
[
    '/usr/local/lib/python3.10/site-packages',  # ← Looks here
    '/home/user/.local/lib/python3.10/site-packages',  # ← And here
    'venv/lib/python3.10/site-packages',  # ← And here (if in venv)
]
```

**Specifically:**
- **Global site-packages:** `/usr/local/lib/python3.10/site-packages/`
- **User site-packages:** `~/.local/lib/python3.10/site-packages/`
- **Virtual env site-packages:** `venv/lib/python3.10/site-packages/`

### Entry Points Storage

**When you install a package:**
```bash
pip install skypilot-templates-spark
```

**What pip creates:**
```
site-packages/
├── skypilot_templates_spark/           # The actual package code
│   ├── __init__.py
│   └── spark/
│       └── setup_spark.sh
│
└── skypilot_templates_spark-1.0.0.dist-info/   # ← METADATA directory
    ├── METADATA                        # Package description
    ├── RECORD                          # List of installed files
    ├── WHEEL                           # Build info
    └── entry_points.txt                # ← ENTRY POINTS DEFINED HERE!
```

**Contents of `entry_points.txt`:**
```ini
# skypilot_templates_spark-1.0.0.dist-info/entry_points.txt

[skypilot.templates]
spark = skypilot_templates_spark
```

**Format:**
```
[group_name]
entry_point_name = module.path:object_or_function
```

### Discovery Process Step-by-Step

**1. Scan all `.dist-info/` directories:**
```python
# Conceptually:
for dist_info in glob('site-packages/*.dist-info'):
    entry_points_file = f'{dist_info}/entry_points.txt'
    if os.path.exists(entry_points_file):
        parse_entry_points(entry_points_file)
```

**2. Parse entry points:**
```ini
# Example entry_points.txt files found:

# From skypilot_templates_spark-1.0.0.dist-info/entry_points.txt
[skypilot.templates]
spark = skypilot_templates_spark

# From skypilot_templates_dask-2.0.0.dist-info/entry_points.txt
[skypilot.templates]
dask = skypilot_templates_dask

# From requests-2.28.0.dist-info/entry_points.txt
# (no skypilot.templates group - ignored)
```

**3. Build entry point objects:**
```python
EntryPoint(
    name='spark',
    value='skypilot_templates_spark',
    group='skypilot.templates'
)
```

**4. Load module and extract path:**
```python
# For the 'spark' entry point:
module = entry_point.load()  # import skypilot_templates_spark
# module.__file__ = '/usr/local/lib/.../skypilot_templates_spark/__init__.py'

template_dir = os.path.dirname(module.__file__)
# template_dir = '/usr/local/lib/.../skypilot_templates_spark/'
```

### Complete Example

**Install two template packages:**
```bash
pip install skypilot-templates-spark
pip install skypilot-templates-dask
```

**Filesystem after install:**
```
site-packages/
├── skypilot_templates_spark/
│   └── spark/
│       └── setup_spark.sh
├── skypilot_templates_spark-1.0.0.dist-info/
│   └── entry_points.txt
│
├── skypilot_templates_dask/
│   └── dask/
│       └── setup_dask.sh
└── skypilot_templates_dask-2.0.0.dist-info/
    └── entry_points.txt
```

**Discovery code execution:**
```python
import importlib.metadata as metadata
import os

for ep in metadata.entry_points().select(group='skypilot.templates'):
    print(f"Found: {ep.name}")
    # Output:
    # Found: spark
    # Found: dask

    module = ep.load()
    template_dir = os.path.dirname(module.__file__)
    print(f"  Templates at: {template_dir}")
    # Output:
    #   Templates at: /usr/local/lib/.../skypilot_templates_spark/
    #   Templates at: /usr/local/lib/.../skypilot_templates_dask/
```

### Visual Flow

```
┌─────────────────────────────────────────────────────────────┐
│ pip install skypilot-templates-spark                        │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ site-packages/                                              │
│ ├── skypilot_templates_spark/      ← Package code          │
│ └── skypilot_templates_spark-1.0.0.dist-info/              │
│     └── entry_points.txt            ← Metadata             │
│         [skypilot.templates]                                │
│         spark = skypilot_templates_spark                    │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ metadata.entry_points()                                     │
│ → Scans all *.dist-info/entry_points.txt                   │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ .select(group='skypilot.templates')                         │
│ → Filters to [skypilot.templates] sections                 │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ entry_point.load()                                          │
│ → import skypilot_templates_spark                          │
│ → Returns module object                                     │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ os.path.dirname(module.__file__)                            │
│ → /usr/local/lib/.../skypilot_templates_spark/             │
│                                                             │
│ Now we know where the templates are!                       │
└─────────────────────────────────────────────────────────────┘
```

### Comparison with Airflow Providers

**This is exactly how Airflow discovers providers:**

| Component | Airflow | SkyPilot |
|-----------|---------|----------|
| **Entry point group** | `apache_airflow_provider` | `skypilot.templates` |
| **Package prefix** | `apache-airflow-providers-*` | `skypilot-templates-*` |
| **Discovery mechanism** | Entry points | Entry points |
| **Distribution** | PyPI | PyPI |
| **Ecosystem size** | 70+ providers | (Future growth) |

**Airflow example:**
```python
# apache-airflow-providers-google/setup.py
setup(
    name='apache-airflow-providers-google',
    entry_points={
        'apache_airflow_provider': [
            'provider_info=airflow.providers.google:get_provider_info'
        ]
    },
)
```

**SkyPilot equivalent:**
```python
# skypilot-templates-spark/setup.py
setup(
    name='skypilot-templates-spark',
    entry_points={
        'skypilot.templates': [
            'spark = skypilot_templates_spark'
        ]
    },
)
```

### Entry Points in Different Install Scenarios

| Scenario | Command | Location | Discovered? |
|----------|---------|----------|-------------|
| **Global** | `sudo pip install pkg` | `/usr/local/lib/python3.10/site-packages/` | ✅ Yes |
| **User** | `pip install --user pkg` | `~/.local/lib/python3.10/site-packages/` | ✅ Yes |
| **Venv** | `pip install pkg` (in venv) | `venv/lib/python3.10/site-packages/` | ✅ Yes |
| **Editable** | `pip install -e .` | Original directory + link in site-packages | ✅ Yes |

### Why Entry Points Are Ideal

**Advantages:**

✅ **Standard Python mechanism** - Used by pytest, Flask, Airflow, setuptools
✅ **Zero configuration** - Automatic discovery
✅ **Package manager agnostic** - Works with pip, conda, poetry
✅ **Virtual environment aware** - Respects active venv
✅ **Development friendly** - Supports editable installs
✅ **Battle-tested** - Proven by Airflow's 70+ provider ecosystem

**This is Python's standard plugin system.**

### Test It Yourself

Create a test package:
```bash
mkdir -p test_pkg/skypilot_templates_test/test
cd test_pkg

# Add template
echo '#!/bin/bash\necho "Hello!"' > skypilot_templates_test/test/hello.sh

# Add __init__.py
echo '__version__ = "0.1.0"' > skypilot_templates_test/__init__.py

# Add setup.py
cat > setup.py << 'EOF'
from setuptools import setup, find_packages
setup(
    name='skypilot-templates-test',
    version='0.1.0',
    packages=find_packages(),
    entry_points={
        'skypilot.templates': ['test = skypilot_templates_test'],
    },
)
EOF

# Install
pip install -e .

# Test discovery
python -c "
import importlib.metadata as m
for ep in m.entry_points().select(group='skypilot.templates'):
    print(f'Found: {ep.name} at {ep.load().__file__}')
"
# Output: Found: test at /path/to/test_pkg/skypilot_templates_test/__init__.py
```

**That's entry points in action!** 🎯
