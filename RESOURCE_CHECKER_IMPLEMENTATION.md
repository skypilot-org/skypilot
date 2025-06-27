# Resource Checker Implementation

## Overview

This implementation adds resource checking functionality before user deletion and extracts the common resource checking logic into a reusable module, as requested.

## Changes Made

### 1. Created New Resource Checker Module

**File:** `sky/utils/resource_checker.py`

This new module provides:
- `check_no_active_resources_for_users(user_operations)` - Check resources for users
- `check_no_active_resources_for_workspaces(workspace_operations)` - Check resources for workspaces
- `_check_active_resources()` - Internal generic function that handles both user and workspace filtering

**Key Features:**
- **Generic Design**: Uses filter factory functions to support both user and workspace resource checking
- **Parallel Execution**: Fetches clusters and managed jobs in parallel for efficiency
- **Comprehensive Error Messages**: Provides detailed error messages listing all active resources
- **Graceful Error Handling**: Handles `ClusterNotUpError` gracefully when the jobs controller is down

### 2. Updated Workspace Core Module

**File:** `sky/workspaces/core.py`

**Changes:**
- Added import for the new `resource_checker` module
- Removed the shallow wrapper functions `_check_workspace_has_no_active_resources()` and `_check_workspaces_have_no_active_resources()`
- Updated all calls to use the `resource_checker` module directly
- Removed unused imports (`concurrent.futures`)

### 3. Updated User Server Module

**File:** `sky/users/server.py`

**Changes:**
- Added import for the new `resource_checker` module
- Updated `_delete_user()` function to check for active resources before deletion
- Added proper error handling that converts `ValueError` to `HTTPException` for API responses
- Replaced the TODO comment with actual implementation

### 4. Created Comprehensive Tests

**File:** `tests/unit_tests/test_sky/utils/test_resource_checker.py`

**Test Coverage:**
- Filter function validation for both users and workspaces
- Single and multiple resource checking scenarios
- Error cases with active clusters and managed jobs
- Edge cases like empty operations lists and missing workspaces
- Error handling for jobs controller unavailability
- Mixed scenarios with some users/workspaces having resources and others not

## Resource Checking Logic

The new system checks for:

1. **Active Clusters**: Uses `global_user_state.get_clusters()` to get all clusters
2. **Active Managed Jobs**: Uses `managed_jobs_core.queue()` to get all active jobs

### Filtering

- **User Filtering**: Filters resources by `user_hash` field
- **Workspace Filtering**: Filters resources by `workspace` field (defaults to `SKYPILOT_DEFAULT_WORKSPACE` if missing)

### Error Messages

The system provides clear, actionable error messages:

- Single resource error: "Cannot delete user 'user-123' because it has 2 active cluster(s): cluster1, cluster2. Please terminate these resources first."
- Multiple resource errors: Lists all affected users/workspaces with their active resources

## Shallow Function Reduction

Based on feedback, the implementation was refactored to eliminate shallow wrapper functions:

- **Removed**: `check_no_active_resources_for_user()` and `check_no_active_resources_for_workspace()` - these were just thin wrappers around the batch functions
- **Removed**: `_check_workspace_has_no_active_resources()` and `_check_workspaces_have_no_active_resources()` from workspace core - these were just calling the resource_checker functions
- **Updated**: All callers now use the batch functions directly: `check_no_active_resources_for_users()` and `check_no_active_resources_for_workspaces()`

This results in a cleaner API with fewer unnecessary layers.

## Benefits

1. **Code Reusability**: Common resource checking logic is now shared between workspace and user management
2. **Consistency**: Both workspace and user deletion now use the same validation logic
3. **Better Error Messages**: Users get comprehensive information about what resources need to be terminated
4. **Parallel Efficiency**: Resource fetching happens in parallel for better performance
5. **Maintainability**: Changes to resource checking logic only need to be made in one place
6. **Simplified API**: Eliminated shallow wrapper functions for a cleaner interface

## Usage Examples

### User Operations
```python
from sky.utils import resource_checker

# Check single user
resource_checker.check_no_active_resources_for_users([('user-123', 'delete')])

# Check multiple users
user_operations = [('user-123', 'delete'), ('user-456', 'delete')]
resource_checker.check_no_active_resources_for_users(user_operations)
```

### Workspace Operations
```python
# Check single workspace
resource_checker.check_no_active_resources_for_workspaces([('my-workspace', 'delete')])

# Check multiple workspaces  
workspace_operations = [('workspace1', 'delete'), ('workspace2', 'update')]
resource_checker.check_no_active_resources_for_workspaces(workspace_operations)
```

## Error Handling

All functions raise `ValueError` with descriptive messages when active resources are found. The calling code should handle these appropriately:

- **CLI/Direct API**: Can show the error message directly to users
- **REST API**: Should convert to appropriate HTTP status codes (e.g., 400 Bad Request)

## Future Enhancements

The generic design allows for easy extension to other resource types or validation scenarios:

1. Add filtering by other criteria (region, cloud provider, etc.)
2. Add different operation types beyond 'delete' and 'update'
3. Add resource type-specific validation logic
4. Add configuration for which resource types to check

## Testing

The implementation includes comprehensive unit tests covering:
- Normal operation scenarios
- Error conditions
- Edge cases
- Filter function validation
- Parallel execution behavior

To run tests:
```bash
pytest tests/unit_tests/test_sky/utils/test_resource_checker.py -v
```