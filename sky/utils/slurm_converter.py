"""Convert a Slurm batch script into a SkyPilot task YAML.

Given the contents of a Slurm script (e.g. one submitted with ``sbatch``),
``convert_slurm_script`` returns an equivalent SkyPilot YAML plus a list of
human-readable warnings describing directives that could not be mapped
automatically.

The mapping follows
https://docs.skypilot.co/en/latest/reference/slurm-migration.html:

    --job-name / -J              -> name
    --nodes / -N                 -> num_nodes
    --gpus-per-node / --gres=gpu -> resources.accelerators
    --gpus / -G                  -> resources.accelerators (divided by nodes)
    --cpus-per-task / -c         -> resources.cpus
    --mem                        -> resources.memory
    SLURM_* env vars             -> SKYPILOT_* env vars (in body)

Directives that do not have a direct SkyPilot equivalent (``--time``,
``--partition``, ``--account``, ``--output``, ``--array``, ...) are preserved
as comments in the generated YAML so the user can decide what to do with them.
"""
import dataclasses
import re
import shlex
from typing import Dict, List, Optional, Tuple

# Mapping of Slurm environment variables to SkyPilot equivalents. Applied to
# the body of the script as a best-effort string replacement.
_ENV_VAR_MAPPING: Dict[str, str] = {
    'SLURM_JOB_NODELIST': 'SKYPILOT_NODE_IPS',
    'SLURM_NODELIST': 'SKYPILOT_NODE_IPS',
    'SLURM_NNODES': 'SKYPILOT_NUM_NODES',
    'SLURM_JOB_NUM_NODES': 'SKYPILOT_NUM_NODES',
    'SLURM_NODEID': 'SKYPILOT_NODE_RANK',
    'SLURM_PROCID': 'SKYPILOT_NODE_RANK',
    'SLURM_GPUS_PER_NODE': 'SKYPILOT_NUM_GPUS_PER_NODE',
    'SLURM_GPUS_ON_NODE': 'SKYPILOT_NUM_GPUS_PER_NODE',
    'SLURM_JOB_ID': 'SKYPILOT_TASK_ID',
    'SLURM_JOBID': 'SKYPILOT_TASK_ID',
}

# Short option -> long option for the SBATCH directives we recognize. Slurm
# accepts both forms interchangeably.
_SHORT_TO_LONG: Dict[str, str] = {
    'J': 'job-name',
    'N': 'nodes',
    'n': 'ntasks',
    'c': 'cpus-per-task',
    'G': 'gpus',
    'p': 'partition',
    't': 'time',
    'o': 'output',
    'e': 'error',
    'D': 'chdir',
    'A': 'account',
    'a': 'array',
    'w': 'nodelist',
    'C': 'constraint',
    'q': 'qos',
    'd': 'dependency',
}


@dataclasses.dataclass
class _ParsedScript:
    directives: Dict[str, str]
    body_lines: List[str]
    # Unknown / unmapped directives, preserved verbatim.
    unknown_directives: List[str]


def _parse_sbatch_tokens(tokens: List[str]) -> List[Tuple[str, str]]:
    """Parse the tokens after ``#SBATCH`` into (key, value) pairs.

    Slurm accepts many forms, e.g. ``--nodes=2``, ``--nodes 2``, ``-N2``,
    ``-N 2``. Flags without values (e.g. ``--exclusive``) get an empty value.
    """
    pairs: List[Tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith('--'):
            body = tok[2:]
            if '=' in body:
                key, value = body.split('=', 1)
                pairs.append((key, value))
                i += 1
            else:
                # Value may be in the next token, or the flag may be boolean.
                if i + 1 < len(tokens) and not tokens[i + 1].startswith('-'):
                    pairs.append((body, tokens[i + 1]))
                    i += 2
                else:
                    pairs.append((body, ''))
                    i += 1
        elif tok.startswith('-') and len(tok) >= 2:
            short = tok[1]
            rest = tok[2:]
            key = _SHORT_TO_LONG.get(short, short)
            if rest:
                pairs.append((key, rest))
                i += 1
            elif i + 1 < len(tokens) and not tokens[i + 1].startswith('-'):
                pairs.append((key, tokens[i + 1]))
                i += 2
            else:
                pairs.append((key, ''))
                i += 1
        else:
            # Unexpected positional token; skip.
            i += 1
    return pairs


def _parse_script(script: str) -> _ParsedScript:
    directives: Dict[str, str] = {}
    unknown: List[str] = []
    body_lines: List[str] = []
    in_header = True
    for raw_line in script.splitlines():
        line = raw_line.rstrip('\n')
        stripped = line.strip()
        if in_header:
            if not stripped:
                # Blank line inside header: keep scanning for more SBATCH
                # directives.
                continue
            if stripped.startswith('#!'):
                # Shebang; drop it.
                continue
            if stripped.startswith('#SBATCH'):
                payload = stripped[len('#SBATCH'):].strip()
                # Strip trailing comment after the directive.
                payload = payload.split('#', 1)[0].strip()
                if not payload:
                    continue
                try:
                    tokens = shlex.split(payload)
                except ValueError:
                    unknown.append(payload)
                    continue
                for key, value in _parse_sbatch_tokens(tokens):
                    directives[key] = value
                continue
            if stripped.startswith('#'):
                # Non-SBATCH comment in the header: drop.
                continue
            # First real command; the rest of the file is the body.
            in_header = False
            body_lines.append(line)
        else:
            body_lines.append(line)
    return _ParsedScript(directives=directives,
                         body_lines=body_lines,
                         unknown_directives=unknown)


def _parse_gpu_spec(value: str) -> Tuple[Optional[str], Optional[int]]:
    """Parse ``[type:]count`` GPU specifications.

    Returns ``(type, count)``. Type may be ``None`` when omitted.
    """
    if not value:
        return None, None
    # Slurm's ``--gres=gpu:type:count`` or ``--gres=gpu:count`` already has the
    # leading ``gpu:`` stripped by the caller.
    parts = value.split(':')
    if len(parts) == 1:
        try:
            return None, int(parts[0])
        except ValueError:
            return parts[0], 1
    # len >= 2: last part is the count.
    try:
        count = int(parts[-1])
        type_parts = parts[:-1]
    except ValueError:
        # No trailing count; assume 1 of the named type.
        return ':'.join(parts), 1
    if not type_parts:
        return None, count
    return ':'.join(type_parts), count


def _normalize_gpu_type(gpu_type: str) -> str:
    """Normalize a Slurm GPU type string to the canonical SkyPilot form."""
    t = gpu_type.strip()
    # Common Slurm naming: a100, a100-40gb, h100, v100, etc. SkyPilot uses
    # upper-case canonical names (H100, A100, V100, ...).
    upper = t.upper()
    # Drop any memory suffix like ``-40GB`` that SkyPilot's main catalogs do
    # not use in the accelerator name.
    upper = re.sub(r'-\d+GB$', '', upper)
    return upper


def _parse_memory(value: str) -> Optional[int]:
    """Parse a Slurm memory string (e.g. ``16G``, ``1024M``) to GB (int)."""
    if not value:
        return None
    match = re.match(r'^(\d+(?:\.\d+)?)\s*([KMGT]?)B?$', value.strip(),
                     re.IGNORECASE)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).upper()
    multiplier = {
        '': 1 / 1024,  # Slurm default unit is MB.
        'K': 1 / (1024 * 1024),
        'M': 1 / 1024,
        'G': 1,
        'T': 1024,
    }.get(unit, 1)
    gb = amount * multiplier
    if gb < 1:
        return 1
    return int(round(gb))


def _substitute_env_vars(text: str) -> str:
    for slurm_var, sky_var in _ENV_VAR_MAPPING.items():
        text = re.sub(r'\$\{?' + slurm_var + r'\}?',
                      lambda m, v=sky_var: '$' + v,
                      text)
    return text


def _strip_srun(body: str) -> Tuple[str, List[str]]:
    """Remove leading ``srun`` invocations from body lines.

    SkyPilot handles multi-node launch itself (via ``SKYPILOT_NODE_IPS`` and
    ``SKYPILOT_NODE_RANK``), so the common pattern ``srun python train.py``
    becomes just ``python train.py``. If the ``srun`` invocation has any flags
    we leave the line unchanged and emit a warning asking the user to review
    it, since translating arbitrary ``srun`` flags is error-prone.
    """
    warnings: List[str] = []
    out_lines: List[str] = []
    for line in body.splitlines():
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]
        if stripped == 'srun' or stripped.startswith('srun '):
            after = stripped[len('srun'):].lstrip()
            if after.startswith('-'):
                warnings.append(
                    f'Left `srun` flags unchanged on line: {stripped!r}. '
                    'SkyPilot distributes work via $SKYPILOT_NODE_IPS and '
                    '$SKYPILOT_NODE_RANK; please translate the flags by '
                    'hand.')
                out_lines.append(line)
            else:
                out_lines.append(indent + after)
        elif stripped.startswith('mpirun ') or stripped.startswith('mpiexec '):
            warnings.append(
                f'Left MPI launcher unchanged on line: {stripped!r}. Make '
                'sure the hostfile uses $SKYPILOT_NODE_IPS.')
            out_lines.append(line)
        else:
            out_lines.append(line)
    return '\n'.join(out_lines), warnings


def _format_yaml_block(key: str, value: str) -> str:
    """Format a multi-line string value as a YAML block scalar."""
    value = value.rstrip('\n')
    if not value:
        return f'{key}: |\n'
    indented = '\n'.join(
        '  ' + line if line else '' for line in value.split('\n'))
    return f'{key}: |\n{indented}\n'


def convert_slurm_script(script: str) -> Tuple[str, List[str]]:
    """Convert a Slurm batch script to a SkyPilot task YAML.

    Args:
        script: The contents of the Slurm script.

    Returns:
        A tuple ``(yaml_text, warnings)``. ``warnings`` lists directives that
        were not mapped and other notes for the user.
    """
    parsed = _parse_script(script)
    directives = parsed.directives
    warnings: List[str] = []

    name: Optional[str] = directives.pop('job-name', None)
    num_nodes: Optional[int] = None
    if 'nodes' in directives:
        nodes_val = directives.pop('nodes')
        # Slurm also allows ``min-max``; pick the minimum.
        nodes_val = nodes_val.split('-')[0]
        try:
            num_nodes = int(nodes_val)
        except ValueError:
            warnings.append(
                f'Could not parse --nodes value {nodes_val!r}; skipping.')

    # CPU: per-node = cpus-per-task * ntasks-per-node (default 1).
    cpus_per_task = directives.pop('cpus-per-task', None)
    ntasks_per_node = directives.pop('ntasks-per-node', None)
    cpus: Optional[int] = None
    if cpus_per_task is not None:
        try:
            c = int(cpus_per_task)
            if ntasks_per_node is not None:
                c *= int(ntasks_per_node)
            cpus = c
        except ValueError:
            warnings.append(
                f'Could not parse --cpus-per-task={cpus_per_task!r}.')
    elif ntasks_per_node is not None:
        try:
            cpus = int(ntasks_per_node)
        except ValueError:
            pass

    # Memory.
    memory_gb: Optional[int] = None
    mem_val = directives.pop('mem', None)
    if mem_val is not None:
        memory_gb = _parse_memory(mem_val)
        if memory_gb is None:
            warnings.append(f'Could not parse --mem={mem_val!r}.')
    elif 'mem-per-cpu' in directives and cpus is not None:
        per_cpu = _parse_memory(directives.pop('mem-per-cpu'))
        if per_cpu is not None:
            memory_gb = per_cpu * cpus
    else:
        directives.pop('mem-per-cpu', None)
        directives.pop('mem-per-gpu', None)

    # GPUs.
    gpu_type: Optional[str] = None
    gpu_count: Optional[int] = None
    if 'gpus-per-node' in directives:
        t, c = _parse_gpu_spec(directives.pop('gpus-per-node'))
        gpu_type = t
        gpu_count = c
    elif 'gres' in directives:
        gres = directives.pop('gres')
        # ``--gres`` can list multiple resources separated by commas; we only
        # handle the GPU entry.
        for entry in gres.split(','):
            entry = entry.strip()
            if entry.lower().startswith('gpu'):
                # Strip the ``gpu`` prefix (with optional ``:``).
                rest = entry[3:]
                rest = rest.lstrip(':')
                t, c = _parse_gpu_spec(rest)
                gpu_type = t
                gpu_count = c
                break
        else:
            warnings.append(f'--gres={gres!r} did not contain a GPU entry; '
                            'skipping.')
    elif 'gpus' in directives:
        t, c = _parse_gpu_spec(directives.pop('gpus'))
        if c is not None and num_nodes and num_nodes > 1:
            per_node, remainder = divmod(c, num_nodes)
            if remainder:
                warnings.append(
                    f'--gpus={c} is not divisible by --nodes={num_nodes}; '
                    f'using {per_node} GPUs per node.')
            gpu_count = per_node or None
        else:
            gpu_count = c
        gpu_type = t

    if gpu_count is not None and gpu_type is None:
        warnings.append(
            'No GPU type was specified in the Slurm script; please edit '
            '`accelerators` in the generated YAML to set one '
            '(e.g. H100, A100, V100).')

    # Directives that SkyPilot does not map directly.
    unsupported_notes: List[Tuple[str, str]] = []

    def _note(flag: str, msg: str) -> None:
        value = directives.pop(flag, None)
        if value is None:
            return
        label = f'--{flag}={value}' if value else f'--{flag}'
        unsupported_notes.append((label, msg))

    _note(
        'time',
        'SkyPilot does not enforce a wall-clock time limit. Use `autostop` '
        'to release idle clusters, or use managed jobs for retries.')
    _note(
        'partition',
        'Slurm partitions do not map directly. Use `resources.infra` to '
        'target a specific cloud/region or Kubernetes context.')
    _note(
        'account', 'SkyPilot does not have accounts. Use workspaces/quotas '
        'in `~/.sky/config.yaml` if needed.')
    _note('qos', 'No direct QoS equivalent in SkyPilot.')
    _note(
        'constraint', 'No direct equivalent; use `resources.instance_type` or '
        '`resources.labels` to constrain placement.')
    _note('reservation', 'Reservations are configured per-cloud in SkyPilot.')
    _note(
        'output', 'SkyPilot captures stdout/stderr automatically; view with '
        '`sky logs` or `sky jobs logs`.')
    _note('error',
          'SkyPilot captures stderr automatically; view with `sky logs`.')
    _note(
        'array', 'Slurm job arrays have no direct equivalent. Launch with a '
        'shell loop over `sky jobs launch --env TASK_ID=$i ...`.')
    _note(
        'dependency',
        'Job dependencies can be modeled with SkyPilot pipelines/DAGs or by '
        'chaining `sky jobs launch` calls.')
    _note('mail-user', 'SkyPilot does not send email notifications.')
    _note('mail-type', 'SkyPilot does not send email notifications.')
    _note('begin', 'Scheduled start times are not supported by SkyPilot.')
    _note('chdir',
          'Set the working directory inside `setup`/`run` or with `workdir:`.')
    _note('exclusive',
          'SkyPilot clusters are allocated exclusively by default.')
    _note(
        'nodelist',
        'Specific node targeting is not supported; use `resources.infra` '
        'or labels instead.')
    _note('exclude', 'Node exclusion is not supported.')
    _note('ntasks',
          'Maps loosely to `num_nodes * ntasks-per-node`; reviewed manually.')

    # Anything still in ``directives`` is unknown / unhandled.
    for flag, value in list(directives.items()):
        label = f'--{flag}={value}' if value else f'--{flag}'
        unsupported_notes.append(
            (label, 'No direct SkyPilot equivalent; please review.'))

    # Body handling: substitute env vars and strip srun wrappers.
    body = '\n'.join(parsed.body_lines).strip('\n')
    body = _substitute_env_vars(body)
    body, srun_warnings = _strip_srun(body)
    warnings.extend(srun_warnings)

    # Assemble the YAML text. We format it manually so that we can interleave
    # comments for unsupported directives.
    lines: List[str] = []
    lines.append('# Generated by `sky utils convert-slurm`. Please review '
                 'before running.')
    lines.append('# Reference: '
                 'https://docs.skypilot.co/en/latest/reference/'
                 'slurm-migration.html')
    lines.append('')

    if unsupported_notes:
        lines.append('# The following Slurm directives could not be mapped '
                     'automatically:')
        for label, msg in unsupported_notes:
            lines.append(f'#   {label}: {msg}')
        lines.append('')

    if parsed.unknown_directives:
        lines.append('# Malformed or unparseable #SBATCH directives:')
        for u in parsed.unknown_directives:
            lines.append(f'#   {u}')
        lines.append('')

    if name:
        lines.append(f'name: {name}')
        lines.append('')
    if num_nodes and num_nodes > 1:
        lines.append(f'num_nodes: {num_nodes}')
        lines.append('')

    resources: List[str] = []
    if gpu_count:
        if gpu_type:
            type_str = _normalize_gpu_type(gpu_type)
        else:
            type_str = '<GPU_TYPE>'
        resources.append(f'  accelerators: {type_str}:{gpu_count}')
    if cpus:
        resources.append(f'  cpus: {cpus}+')
    if memory_gb:
        resources.append(f'  memory: {memory_gb}+')
    if resources:
        lines.append('resources:')
        lines.extend(resources)
        lines.append('')

    if body.strip():
        lines.append('run: |')
        for line in body.splitlines():
            lines.append('  ' + line if line else '')
        lines.append('')
    else:
        lines.append('run: |')
        lines.append('  echo "Hello from SkyPilot"')
        lines.append('')

    yaml_text = '\n'.join(lines).rstrip('\n') + '\n'
    return yaml_text, warnings
