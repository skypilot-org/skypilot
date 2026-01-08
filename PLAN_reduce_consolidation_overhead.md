# Plan: Reduce Consolidation Mode Overhead

## Problem Analysis

### Current Architecture

In **normal mode**, managed job operations communicate with the jobs controller (a separate VM) via SSH by:
1. Generating Python code strings using `ManagedJobCodeGen`
2. Executing the code on the remote controller via `backend.run_on_head(handle, code)`
3. The remote controller already has Sky modules loaded

In **consolidation mode**, the jobs controller runs within the API server process. However, the current implementation still:
1. Generates Python code strings using `ManagedJobCodeGen`
2. Executes via `LocalProcessCommandRunner` which spawns a **new subprocess**
3. The subprocess must re-import all Sky modules (~1 second overhead per operation)

### Key Files and Code Paths

**Code Generation:**
- `sky/jobs/utils.py:2155-2393` - `ManagedJobCodeGen` class generates Python code strings
- The `_PREFIX` (lines 2162-2169) always includes imports:
  ```python
  import sys
  from sky.jobs import utils
  from sky.jobs import state as managed_job_state
  from sky.jobs import constants as managed_job_constants
  ```

**Execution Path in Consolidation Mode:**
```
queue() [sky/jobs/server/core.py:934-944]
  → ManagedJobCodeGen.get_job_table() [generates code string]
  → backend.run_on_head(handle, code)
  → LocalResourcesHandle.get_command_runners() [returns LocalProcessCommandRunner]
  → LocalProcessCommandRunner.run() [sky/utils/command_runner.py:1389-1450]
  → log_lib.run_with_log(command_str, shell=True) [NEW SUBPROCESS SPAWNED]
  → subprocess imports Sky modules (~1 second overhead)
```

**Operations Affected:**
- `sky jobs queue` → `ManagedJobCodeGen.get_job_table()`
- `sky jobs cancel` → `ManagedJobCodeGen.cancel_jobs_by_id()` / `cancel_job_by_name()`
- `sky jobs logs` → `ManagedJobCodeGen.stream_logs()`
- Job status refresh daemon → `update_managed_jobs_statuses()`
- And others...

### Overhead Source

Each operation in consolidation mode spawns a fresh Python subprocess that:
1. Starts a new Python interpreter
2. Imports `sky.jobs.utils` and related modules
3. Executes the actual function (very fast)
4. Exits

The import overhead is ~1 second per operation, making consolidation mode feel sluggish.

---

## Solution Design

### Option A: Direct Function Calls (Recommended)

**Approach:**
Instead of generating code strings and running them as subprocesses, directly call the underlying Python functions when in consolidation mode.

**Implementation Strategy:**

1. **Create a dispatch layer** that abstracts over the two execution modes:
   - **Remote mode**: Generate code + run_on_head (existing approach)
   - **Local mode**: Direct function call (new approach)

2. **Modify call sites** to check if using `LocalResourcesHandle` and dispatch accordingly.

**Example refactoring for `queue()`:**

Before (current):
```python
# sky/jobs/server/core.py:934-944
code = managed_job_utils.ManagedJobCodeGen.get_job_table(
    skip_finished, accessible_workspaces, job_ids, ...)
returncode, job_table_payload, stderr = backend.run_on_head(
    handle, code, require_outputs=True, ...)
```

After (proposed):
```python
# sky/jobs/server/core.py
if isinstance(handle, backends.LocalResourcesHandle):
    # Direct function call - no subprocess overhead
    job_table_payload = managed_job_utils.dump_managed_job_queue(
        skip_finished, accessible_workspaces, job_ids, ...)
    returncode, stderr = 0, ''
else:
    # Remote execution via codegen
    code = managed_job_utils.ManagedJobCodeGen.get_job_table(
        skip_finished, accessible_workspaces, job_ids, ...)
    returncode, job_table_payload, stderr = backend.run_on_head(
        handle, code, require_outputs=True, ...)
```

**Advantages:**
- Complete elimination of subprocess overhead in consolidation mode
- Type-safe direct function calls
- Reuses existing functions (e.g., `dump_managed_job_queue()`, `cancel_jobs_by_id()`)
- Easy to understand and maintain

**Disadvantages:**
- Need to modify multiple call sites
- Some code duplication in dispatching logic

---

### Option B: Helper Abstraction Layer

Create a helper module that encapsulates the dispatch logic:

```python
# sky/jobs/executor.py (new file)

from sky import backends
from sky.jobs import utils as managed_job_utils

def execute_job_operation(
    handle: backends.CloudVmRayResourceHandle,
    backend: backends.CloudVmRayBackend,
    operation: str,
    **kwargs
) -> Tuple[int, str, str]:
    """Execute a job operation, dispatching to direct call or remote execution.

    For LocalResourcesHandle (consolidation mode), directly calls the function.
    For CloudVmRayResourceHandle (normal mode), uses codegen + run_on_head.
    """
    if isinstance(handle, backends.LocalResourcesHandle):
        return _execute_local(operation, **kwargs)
    else:
        return _execute_remote(handle, backend, operation, **kwargs)

def _execute_local(operation: str, **kwargs) -> Tuple[int, str, str]:
    """Direct function call for consolidation mode."""
    try:
        if operation == 'get_job_table':
            result = managed_job_utils.dump_managed_job_queue(**kwargs)
        elif operation == 'cancel_jobs_by_id':
            result = managed_job_utils.cancel_jobs_by_id(**kwargs)
        # ... other operations
        return 0, result, ''
    except Exception as e:
        return 1, '', str(e)

def _execute_remote(handle, backend, operation: str, **kwargs) -> Tuple[int, str, str]:
    """Code generation + run_on_head for normal mode."""
    if operation == 'get_job_table':
        code = managed_job_utils.ManagedJobCodeGen.get_job_table(**kwargs)
    elif operation == 'cancel_jobs_by_id':
        code = managed_job_utils.ManagedJobCodeGen.cancel_jobs_by_id(**kwargs)
    # ... other operations
    return backend.run_on_head(handle, code, require_outputs=True, ...)
```

**Usage at call site:**
```python
from sky.jobs import executor

returncode, job_table_payload, stderr = executor.execute_job_operation(
    handle, backend, 'get_job_table',
    skip_finished=skip_finished,
    accessible_workspaces=accessible_workspaces,
    ...)
```

**Advantages:**
- Clean abstraction
- Single place to manage dispatch logic
- Call sites are simplified

**Disadvantages:**
- Adds another layer of indirection
- Need to keep two implementations in sync

---

### Option C: In-Process Execution with exec()

Keep the codegen approach but execute the generated Python code in-process using `exec()`:

```python
# In LocalProcessCommandRunner or a new method

def run_in_process(self, code: str) -> Tuple[int, str, str]:
    """Execute Python code in the current process."""
    import io
    import sys
    from contextlib import redirect_stdout, redirect_stderr

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()

    try:
        # Create isolated namespace
        namespace = {}
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            exec(code, namespace)
        return 0, stdout_capture.getvalue(), stderr_capture.getvalue()
    except Exception as e:
        return 1, '', str(e)
```

**Advantages:**
- Minimal changes to existing code
- Keeps the codegen approach for both modes (consistency)
- Modules are already imported in the API server

**Disadvantages:**
- `exec()` has security implications
- Namespace isolation issues
- The codegen still includes unnecessary import statements
- Less clean than direct function calls

---

## Recommended Implementation Plan

I recommend **Option A (Direct Function Calls)** with the helper pattern from **Option B** for cleaner code organization.

### Phase 1: Core Infrastructure

1. **Create `sky/jobs/executor.py`** - A dispatch module that:
   - Detects handle type (LocalResourcesHandle vs CloudVmRayResourceHandle)
   - For local handles, calls functions directly
   - For remote handles, uses codegen + run_on_head

2. **Define operation mappings** - Map operation names to:
   - Direct function calls (for local execution)
   - Codegen methods (for remote execution)

### Phase 2: Migrate Core Operations

Migrate the most frequently used operations first:

1. `get_job_table` → `dump_managed_job_queue()`
2. `cancel_jobs_by_id` → `cancel_jobs_by_id()`
3. `cancel_job_by_name` → `cancel_job_by_name()`
4. `get_all_job_ids_by_name` → `state.get_all_job_ids_by_name()`

### Phase 3: Migrate Remaining Operations

5. `stream_logs` → `stream_logs()`
6. `set_pending` → `state.set_job_info()` + `state.set_pending()`
7. `get_version` → Direct constant access
8. Other operations as needed

### Phase 4: Testing and Cleanup

1. Add unit tests for the executor module
2. Add integration tests for consolidation mode
3. Update documentation
4. Consider removing codegen methods that are no longer needed (or keep for backward compatibility)

---

## Files to Modify

| File | Changes |
|------|---------|
| `sky/jobs/executor.py` | **NEW** - Dispatch layer for job operations |
| `sky/jobs/server/core.py` | Update `queue()`, `cancel()`, etc. to use executor |
| `sky/jobs/server/utils.py` | Update operations using run_on_head |
| `sky/backends/cloud_vm_ray_backend.py` | Minor updates if needed |
| `tests/unit_tests/test_sky/jobs/` | Add tests for executor |

---

## Backward Compatibility Considerations

1. **Non-consolidation mode**: No changes - continues using codegen + run_on_head
2. **Consolidation mode**: Behavior identical, just faster
3. **API versioning**: No API changes required as this is internal optimization
4. **Rolling updates**: The executor should handle mixed scenarios gracefully

---

## Performance Expectations

- **Current**: ~1 second overhead per operation in consolidation mode (due to subprocess + import)
- **After**: Near-zero overhead (direct function calls)
- **Estimated improvement**: ~1 second per job operation

---

## Alternative: Simpler Inline Approach

If you prefer minimal file changes, you can simply add inline checks at each call site:

```python
# In sky/jobs/server/core.py queue()
if isinstance(handle, backends.LocalResourcesHandle):
    # Direct call
    job_table_payload = managed_job_utils.dump_managed_job_queue(...)
    returncode, stderr = 0, ''
else:
    # Codegen approach
    code = managed_job_utils.ManagedJobCodeGen.get_job_table(...)
    returncode, job_table_payload, stderr = backend.run_on_head(...)
```

This is simpler but less elegant than the executor abstraction.
