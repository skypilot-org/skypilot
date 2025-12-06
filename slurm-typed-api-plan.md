# Plan: Add Strongly Typed Return Values to SlurmClient

## Problem Statement

The SlurmClient in `sky/adaptors/slurm.py` returns untyped data (strings, dicts, parallel lists) that creates type safety issues:

1. **Line 127 TODO**: `info_nodes()` returns `List[str]` containing JSON that must be manually parsed
2. **High-risk parsing**: Call sites use brittle `.split()` parsing and dict key access without validation
3. **Type casting errors**: Methods return `Dict[str, str]` requiring unsafe casting like `int(node_details.get('CPUTot', '0'))`
4. **Parallel list anti-pattern**: `get_job_nodes()` returns `Tuple[List[str], List[str]]` requiring error-prone `zip()`

## Solution Approach

Create strongly typed return values using NamedTuple (following existing patterns in `sky/catalog/common.py` and `sky/server/versions.py`). Move parsing into the client methods to eliminate type safety issues at call sites.

## Type Definitions

Create new file: **`sky/adaptors/slurm_types.py`**

```python
from typing import NamedTuple, Optional, Tuple
import enum

class NodeState(enum.Enum):
    """Slurm node states."""
    ALLOCATED = 'alloc'
    IDLE = 'idle'
    MIXED = 'mix'
    DOWN = 'down'
    DRAINED = 'drain'
    DRAINING = 'drng'
    FAIL = 'fail'
    FAILING = 'failg'
    FUTURE = 'futr'
    MAINT = 'maint'
    PERFCTRS = 'npc'
    PLANNED = 'plnd'
    POWER_DOWN = 'pow_dn'
    POWER_UP = 'pow_up'
    RESERVED = 'resv'
    COMPLETING = 'comp'
    UNKNOWN = 'unk'

    @classmethod
    def from_string(cls, state_str: str) -> 'NodeState':
        """Parse from sinfo output, handles '*' suffix."""
        clean = state_str.rstrip('*').lower()
        for state in cls:
            if clean == state.value:
                return state
        return cls.UNKNOWN

class JobState(enum.Enum):
    """Slurm job states."""
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    SUSPENDED = 'SUSPENDED'
    COMPLETING = 'COMPLETING'
    COMPLETED = 'COMPLETED'
    CANCELLED = 'CANCELLED'
    FAILED = 'FAILED'
    TIMEOUT = 'TIMEOUT'
    NODE_FAIL = 'NODE_FAIL'
    PREEMPTED = 'PREEMPTED'
    BOOT_FAIL = 'BOOT_FAIL'
    DEADLINE = 'DEADLINE'
    OUT_OF_MEMORY = 'OUT_OF_MEMORY'

    @classmethod
    def from_string(cls, state_str: str) -> Optional['JobState']:
        """Parse from squeue output."""
        if not state_str:
            return None
        clean = state_str.strip().upper()
        for state in cls:
            if clean == state.value:
                return state
        return None

class NodeInfo(NamedTuple):
    """Node information from sinfo (replaces JSON strings)."""
    node: str
    state: NodeState
    gres: str
    cpus: int
    memory_mb: int
    partition: str

    @property
    def memory_gb(self) -> float:
        """Memory in gigabytes."""
        return self.memory_mb / 1024.0

    def has_gpu(self) -> bool:
        """Check if this node has GPU resources."""
        return 'gpu:' in self.gres.lower()

    def get_gpu_info(self) -> Optional[Tuple[str, int]]:
        """Extract (gpu_type, count) from GRES string.

        Examples:
            'gpu:a100:8' -> ('a100', 8)
            'gpu:nvidia_h100_80gb_hbm3:8' -> ('nvidia_h100_80gb_hbm3', 8)
        """
        import re
        match = re.match(r'^gpu:([^:]+):(\d+)', self.gres)
        return (match.group(1), int(match.group(2))) if match else None

class NodeDetails(NamedTuple):
    """Detailed node info from scontrol (replaces Dict[str, str])."""
    node_name: str
    cpu_total: int
    real_memory_mb: int
    state: str
    node_addr: Optional[str]
    raw_data: dict  # Escape hatch for untyped fields

class NodeIPPair(NamedTuple):
    """Node and IP (replaces parallel lists)."""
    node: str
    ip: str
```

## Critical Files to Modify

### 1. `sky/adaptors/slurm_types.py` (NEW)
Create type definitions as shown above.

### 2. `sky/adaptors/slurm.py`
Update 4 methods to return typed values:

#### `info_nodes()` (line 128)
**Change**: `List[str]` → `List[slurm_types.NodeInfo]`

```python
from sky.adaptors import slurm_types
import json

def info_nodes(self) -> List[slurm_types.NodeInfo]:
    """Get Slurm node information.

    Returns:
        List of NodeInfo objects with structured, typed data.
    """
    cmd = ('sinfo -h --Node -o '
           '\'{ "node": "%N", "state": "%t", "gres": "%G", '
           '"cpus": "%c", "memory": "%m", "partition": "%P" }\'')
    rc, stdout, stderr = self._runner.run(cmd,
                                          require_outputs=True,
                                          stream_logs=False)
    subprocess_utils.handle_returncode(
        rc, cmd, 'Failed to get Slurm node information.', stderr=stderr)

    nodes = []
    for line in stdout.splitlines():
        try:
            data = json.loads(line)
            node_info = slurm_types.NodeInfo(
                node=data['node'],
                state=slurm_types.NodeState.from_string(data['state']),
                gres=data['gres'],
                cpus=int(data['cpus']),
                memory_mb=int(data['memory']),
                partition=data['partition']
            )
            nodes.append(node_info)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f'Failed to parse node info from line: {line}. Error: {e}')
            continue

    return nodes
```

**Remove TODO comment on line 127**: `# TODO(kevin): Give the return value a proper type.`

#### `node_details()` (line 149)
**Change**: `Dict[str, str]` → `slurm_types.NodeDetails`

```python
def node_details(self, node_name: str) -> slurm_types.NodeDetails:
    """Get detailed Slurm node information.

    Returns:
        NodeDetails object with typed fields.
    """
    def _parse_scontrol_node_output(output: str) -> Dict[str, str]:
        # Keep existing parsing logic
        node_info = {}
        parts = output.split()
        for part in parts:
            if '=' in part:
                key, value = part.split('=', 1)
                value = value.strip('\'"')
                node_info[key] = value
        return node_info

    cmd = f'scontrol show node {node_name}'
    rc, node_details_str, _ = self._runner.run(cmd,
                                                require_outputs=True,
                                                stream_logs=False)
    subprocess_utils.handle_returncode(
        rc,
        cmd,
        f'Failed to get detailed node information for {node_name}.',
        stderr=node_details_str)

    raw_data = _parse_scontrol_node_output(node_details_str)

    return slurm_types.NodeDetails(
        node_name=node_name,
        cpu_total=int(raw_data.get('CPUTot', '0')),
        real_memory_mb=int(raw_data.get('RealMemory', '0')),
        state=raw_data.get('State', 'UNKNOWN'),
        node_addr=raw_data.get('NodeAddr'),
        raw_data=raw_data
    )
```

#### `get_job_nodes()` (line 286)
**Change**: `Tuple[List[str], List[str]]` → `List[slurm_types.NodeIPPair]`

```python
def get_job_nodes(self, job_id: str, wait: bool = True) -> List[slurm_types.NodeIPPair]:
    """Get the list of nodes and their IPs for a given job ID.

    Args:
        job_id: The Slurm job ID.
        wait: If True, wait for nodes to be allocated before returning.

    Returns:
        List of NodeIPPair objects containing node names and IPs.
    """
    if wait:
        self.wait_for_job_nodes(job_id)

    cmd = (
        f'squeue -h --jobs {job_id} -o "%N" | tr \',\' \'\\n\' | '
        f'while read node; do '
        f'ip=$(scontrol show node=$node | grep NodeAddr= | '
        f'awk -F= \'{{print $2}}\' | awk \'{{print $1}}\'); '
        f'echo "$node $ip"; '
        f'done')
    rc, stdout, stderr = self._runner.run(cmd,
                                          require_outputs=True,
                                          stream_logs=False)
    subprocess_utils.handle_returncode(
        rc, cmd, f'Failed to get nodes for job {job_id}.', stderr=stderr)
    logger.debug(f'Successfully got nodes for job {job_id}: {stdout}')

    node_ip_pairs = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if line:
            parts = line.split()
            if len(parts) >= 2:
                node_name = parts[0]
                node_ip = parts[1]
                node_ip_pairs.append(slurm_types.NodeIPPair(node_name, node_ip))

    if not node_ip_pairs:
        raise RuntimeError(
            f'No nodes found for job {job_id}. '
            f'The job may have terminated or the output was empty.')

    return node_ip_pairs
```

**Remove TODO comment on line 308**: About using JSON output.

#### `get_job_state()` (line 213)
**Change**: `Optional[str]` → `Optional[slurm_types.JobState]`

```python
def get_job_state(self, job_id: str) -> Optional[slurm_types.JobState]:
    """Get the state of a Slurm job.

    Args:
        job_id: The Slurm job ID.

    Returns:
        The job state as an enum, or None if job not found.
    """
    cmd = f'squeue -h --only-job-state --jobs {job_id} -o "%T"'
    rc, stdout, stderr = self._runner.run(cmd,
                                          require_outputs=True,
                                          stream_logs=False)
    if rc != 0:
        logger.debug(f'Failed to get job state for job {job_id}: {stderr}')
        return None

    state_str = stdout.strip()
    return slurm_types.JobState.from_string(state_str)
```

### 3. `sky/provision/slurm/utils.py`

#### Add import at top
```python
from sky.adaptors import slurm_types
```

#### Update `_get_slurm_node_info_list()` (lines 426-436)

**OLD**:
```python
for line in sinfo_output:
    try:
        node_data = json.loads(line)
    except json.JSONDecodeError:
        logger.warning(f'Failed to parse JSON from sinfo output: {line}')
        continue

    node_name = node_data['node']
    state = node_data['state']
    gres_str = node_data['gres']
    partition = node_data['partition']
```

**NEW**:
```python
sinfo_output = slurm_client.info_nodes()  # Now returns List[NodeInfo]

for node_info in sinfo_output:
    node_name = node_info.node
    state = node_info.state.value  # Get string value from enum
    gres_str = node_info.gres
    partition = node_info.partition
```

#### Update `check_instance_fits()` (lines 276-290) - CRITICAL

**OLD (BRITTLE CODE)**:
```python
def check_cpu_mem_fits(candidate_instance_type: SlurmInstanceType,
                       node_list: List[str]) -> Tuple[bool, Optional[str]]:
    max_cpu = 0
    max_mem_gb = 0.0

    for node_line in node_list:
        parts = node_line.split()
        # parts: [NodeName, State, GRES, CPUs, MemoryMB, Partition]
        if len(parts) >= 5:
            try:
                node_cpus = int(parts[3])
            except ValueError:
                node_cpus = 0
            try:
                node_mem_gb = float(parts[4]) / 1024.0
            except ValueError:
                node_mem_gb = 0.0
        else:
            node_cpus = 0
            node_mem_gb = 0.0
```

**NEW (TYPE-SAFE)**:
```python
def check_cpu_mem_fits(candidate_instance_type: SlurmInstanceType,
                       node_list: List[slurm_types.NodeInfo]) -> Tuple[bool, Optional[str]]:
    max_cpu = 0
    max_mem_gb = 0.0

    for node_info in node_list:
        # Direct typed access - no parsing!
        node_cpus = node_info.cpus
        node_mem_gb = node_info.memory_gb

        if node_cpus > max_cpu:
            max_cpu = node_cpus
            max_mem_gb = node_mem_gb

        if (node_cpus >= candidate_instance_type.cpus and
                node_mem_gb >= candidate_instance_type.memory):
            return True, None

    return False, (f'Max found: {max_cpu} CPUs, '
                   f'{common_utils.format_float(max_mem_gb)}G memory')
```

#### Update partition filtering (lines 319-333)

**OLD**:
```python
nodes = client.info_nodes()  # Returns List[str]
filtered = []
for n in nodes:
    parts = n.split()
    if len(parts) < 6:
        continue
    node_partition = parts[5]
```

**NEW**:
```python
nodes = client.info_nodes()  # Returns List[NodeInfo]

if partition is not None:
    filtered = []
    for node_info in nodes:
        node_partition = node_info.partition
        # Strip '*' from default partition name
        if (node_partition.endswith('*') and
                node_partition[:-1] == default_partition):
            node_partition = node_partition[:-1]
        if node_partition == partition:
            filtered.append(node_info)
    nodes = filtered
```

#### Update node details usage (lines 493-495)

**OLD**:
```python
node_details = slurm_client.node_details(node_name)  # Dict[str, str]
vcpu_total = int(node_details.get('CPUTot', '0'))
mem_gb = float(node_details.get('RealMemory', '0')) / 1024.0
```

**NEW**:
```python
node_details = slurm_client.node_details(node_name)  # NodeDetails
vcpu_total = node_details.cpu_total  # Already int
mem_gb = node_details.real_memory_mb / 1024.0
```

### 4. `sky/provision/slurm/instance.py`

#### Add import at top
```python
from sky.adaptors import slurm_types
```

#### Update job node usage (lines 123, 224, 358)

**OLD**:
```python
nodes, _ = client.get_job_nodes(job_id, wait=True)

# Later at line 358:
nodes, node_ips = client.get_job_nodes(job_id, wait=False)
instances = {
    f'{slurm_utils.instance_id(job_id, node)}': [
        common.InstanceInfo(
            instance_id=slurm_utils.instance_id(job_id, node),
            internal_ip=node_ip,
            ...
        )
    ] for node, node_ip in zip(nodes, node_ips)
}
```

**NEW**:
```python
node_ip_pairs = client.get_job_nodes(job_id, wait=True)
nodes = [pair.node for pair in node_ip_pairs]  # Extract if needed

# Later at line 358:
node_ip_pairs = client.get_job_nodes(job_id, wait=False)
instances = {
    f'{slurm_utils.instance_id(job_id, pair.node)}': [
        common.InstanceInfo(
            instance_id=slurm_utils.instance_id(job_id, pair.node),
            internal_ip=pair.ip,
            ...
        )
    ] for pair in node_ip_pairs
}
```

#### Update job state checks (lines 249-262)

**OLD**:
```python
state = self.get_job_state(job_id)

if state in ('COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT'):
    raise RuntimeError(...)
```

**NEW**:
```python
state = self.get_job_state(job_id)  # Returns Optional[JobState]

terminal_states = {
    slurm_types.JobState.COMPLETED,
    slurm_types.JobState.CANCELLED,
    slurm_types.JobState.FAILED,
    slurm_types.JobState.TIMEOUT,
}
if state in terminal_states:
    raise RuntimeError(f'Job {job_id} terminated with state {state.value}...')
```

## Implementation Steps

1. **Create type definitions** (~2h)
   - Create `sky/adaptors/slurm_types.py` with all NamedTuple and Enum classes
   - Add helper methods (has_gpu, get_gpu_info, memory_gb)
   - Import json module in slurm.py

2. **Update SlurmClient methods** (~3h)
   - Update `info_nodes()` - parse JSON internally, return List[NodeInfo]
   - Update `node_details()` - convert strings to ints, return NodeDetails
   - Update `get_job_nodes()` - return List[NodeIPPair]
   - Update `get_job_state()` - return Optional[JobState]
   - Remove TODO comments on lines 127 and 308

3. **Update call sites** (~3h)
   - Update `_get_slurm_node_info_list()` - remove JSON parsing
   - Update `check_instance_fits()` - eliminate brittle string splitting
   - Update instance.py - use NodeIPPair, eliminate zip()
   - Update `wait_for_job_nodes()` - use JobState enum

4. **Validation** (~2h)
   - Run `mypy sky/adaptors/slurm.py sky/provision/slurm/`
   - Grep for missed call sites: `grep -r "info_nodes\|get_job_nodes" sky/`
   - Test if Slurm cluster available

## Key Benefits

1. **Type safety**: Type checker catches errors at development time
2. **No manual parsing**: JSON parsing and string splitting moved into client
3. **No casting errors**: Types already correct (int, float, enum)
4. **Better IDE support**: Autocomplete and inline docs for fields
5. **Eliminates high-risk code**: Removes brittle positional parsing in `check_instance_fits()`
6. **Self-documenting**: NamedTuple fields show exactly what data is available

## Risk Mitigation

- **Low risk**: Internal API only, no external consumers found
- **Graceful degradation**: UNKNOWN enum values for unrecognized states
- **Escape hatch**: raw_data field in NodeDetails for debugging
- **Atomic change**: All updates in single PR, no deprecation needed
- **Comprehensive error handling**: Try/except around JSON parsing with logging

## Summary

This change addresses the TODO on line 127 and systematically improves type safety throughout the Slurm adaptor by:
- Creating strongly typed return values (NamedTuple)
- Moving parsing logic into the client
- Eliminating brittle string splitting and dict key access
- Following established codebase patterns (NamedTuple in catalog/common.py)
