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
    # ``SLURM_ARRAY_TASK_ID`` only makes sense once the user has converted the
    # job array to a ``sky jobs launch --env TASK_ID=$i`` shell loop (per the
    # migration guide). Substituting to ``TASK_ID`` keeps the body usable.
    'SLURM_ARRAY_TASK_ID': 'TASK_ID',
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


# SBATCH boolean flags that take no value. Without this, ``#SBATCH --exclusive
# user`` would parse ``user`` as the value of ``--exclusive``.
_SBATCH_BOOLEAN_FLAGS = frozenset({
    'exclusive',
    'verbose',
    'quiet',
    'hold',
    'requeue',
    'no-requeue',
    'spread-job',
    'test-only',
    'use-min-nodes',
    'wait-all-nodes',
    'contiguous',
    'no-kill',
    'kill-on-invalid-dep',
    'parsable',
    'overcommit',
    'oversubscribe',
    'reboot',
    'get-user-env',
})


def _parse_sbatch_tokens(tokens: List[str]) -> List[Tuple[str, str]]:
    """Parse the tokens after ``#SBATCH`` into (key, value) pairs.

    Slurm accepts many forms, e.g. ``--nodes=2``, ``--nodes 2``, ``-N2``,
    ``-N 2``. Flags listed in ``_SBATCH_BOOLEAN_FLAGS`` take no value (so the
    next token isn't consumed); other flags without ``=`` take the next token
    as their value.
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
            elif body in _SBATCH_BOOLEAN_FLAGS:
                pairs.append((body, ''))
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
            elif key in _SBATCH_BOOLEAN_FLAGS:
                pairs.append((key, ''))
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


def _join_continuations(lines: List[str]) -> List[str]:
    """Join shell line continuations (``\\`` at end of line) into single lines.

    Slurm scripts often spell long ``srun`` invocations across several lines
    using ``\\`` continuations, e.g.::

        srun \\
          --ntasks-per-node=8 \\
          python train.py

    The body translator inspects one line at a time, so we collapse these
    before handing them off so the srun translator sees the full command.
    """
    merged: List[str] = []
    buffer: Optional[str] = None
    for line in lines:
        # Strip a trailing ``\`` (with optional whitespace before it).
        m = re.match(r'^(.*?)\s*\\\s*$', line)
        if m:
            piece = m.group(1)
            if buffer is None:
                buffer = piece
            else:
                buffer += ' ' + piece.lstrip()
            continue
        if buffer is not None:
            merged.append(buffer + ' ' + line.lstrip())
            buffer = None
        else:
            merged.append(line)
    if buffer is not None:
        merged.append(buffer)
    return merged


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
        # Bare ``--gres=gpu`` with no count is sometimes accepted by Slurm
        # and means one GPU. Default to that rather than skipping silently.
        return None, 1
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


def _parse_slurm_time(value: str) -> Optional[int]:
    """Parse a Slurm ``--time`` value into whole minutes.

    Slurm accepts ``MM``, ``MM:SS``, ``HH:MM:SS``, ``DD-HH``, ``DD-HH:MM`` and
    ``DD-HH:MM:SS``. Returns minutes rounded up, or ``None`` on parse failure.
    """
    value = value.strip()
    if not value:
        return None
    days = 0
    rest = value
    if '-' in value:
        day_str, rest = value.split('-', 1)
        try:
            days = int(day_str)
        except ValueError:
            return None
    parts = rest.split(':') if rest else []
    try:
        nums = [int(p) for p in parts] if parts else [0]
    except ValueError:
        return None

    if '-' in value:
        # DD-HH / DD-HH:MM / DD-HH:MM:SS
        while len(nums) < 3:
            nums.append(0)
        hours, minutes, seconds = nums[0], nums[1], nums[2]
    elif len(nums) == 1:
        # Bare minutes.
        hours, minutes, seconds = 0, nums[0], 0
    elif len(nums) == 2:
        # MM:SS.
        hours, minutes, seconds = 0, nums[0], nums[1]
    elif len(nums) == 3:
        hours, minutes, seconds = nums
    else:
        return None
    total_minutes = days * 24 * 60 + hours * 60 + minutes
    if seconds > 0:
        total_minutes += 1  # Round up to capture the partial minute.
    return total_minutes if total_minutes > 0 else None


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
        # Match ``$VAR`` only when not followed by another word char (so
        # ``$SLURM_JOB_IDX`` doesn't silently become ``$SKYPILOT_TASK_IDX``),
        # and ``${VAR}`` as a full brace-delimited ref.
        pattern = (r'\$' + re.escape(slurm_var) + r'(?!\w)|\$\{' +
                   re.escape(slurm_var) + r'\}')
        text = re.sub(pattern, lambda m, v=sky_var: '$' + v, text)
    return text


def _shell_safe_quote(token: str) -> str:
    """Quote ``token`` for the shell only if it contains whitespace or
    metacharacters that would break reparsing.

    Unlike ``shlex.quote``, leaves ``$VAR``/``~``/globs/etc. untouched so the
    shell still expands them when the translated line runs.
    """
    if not token:
        return "''"
    # If the token contains whitespace, quotes, backticks, subshells, redirects
    # or pipes, we must quote it.
    if re.search(r'[\s<>|&;\'"`\\()]', token):
        return shlex.quote(token)
    return token


# srun flags that do not affect the translated command (they either describe
# resources SkyPilot already allocates at the cluster level, or they are
# launcher-specific behaviors that are a no-op once we drop ``srun`` itself).
_SRUN_FLAGS_TO_DROP = {
    # Resource / placement selectors -- already expressed by the cluster spec.
    'gres',
    'gpus',
    'gpus-per-task',
    'gpus-per-node',
    'gpu-bind',
    'cpus-per-task',
    'cpu-bind',
    'threads-per-core',
    'hint',
    'mem',
    'mem-per-cpu',
    'mem-per-gpu',
    'mem-bind',
    # Slurm launcher / MPI behaviors.
    'mpi',
    'label',
    'unbuffered',
    'kill-on-bad-exit',
    'propagate',
    'pty',
    'multi-prog',
    'preserve-env',
    'resv-ports',
    'distribution',
    'exclusive',
    'oversubscribe',
    'overlap',
    'overcommit',
    'exact',
    # Routing / scheduling -- doesn't apply under SkyPilot.
    'partition',
    'account',
    'qos',
    'constraint',
    'reservation',
    'cluster',
    'time',
    'time-min',
    'deadline',
    'begin',
    'dependency',
    'priority',
    'nice',
    'hold',
    'no-requeue',
    'requeue',
    'wait',
    # Node targeting -- SkyPilot chooses the nodes.
    'nodelist',
    'nodefile',
    'exclude',
    'relative',
    'switches',
    # I/O and env.
    'output',
    'error',
    'input',
    'open-mode',
    'chdir',
    'export',
    'export-file',
    'job-name',
    'comment',
    'network',
    'quiet',
    'slurmd-debug',
    'verbose',
}

# Flags that take no value. Without this set, a boolean short flag like ``-l``
# or ``-v`` followed by the command (``srun -l python x.py``) would greedily
# consume ``python`` as the flag's value and drop it from the command.
_SRUN_BOOLEAN_FLAGS = frozenset({
    'label',
    'unbuffered',
    'quiet',
    'verbose',
    'disable-status',
    'kill-on-bad-exit',
    'no-allocate',
    'overcommit',
    'oversubscribe',
    'exclusive',
    'overlap',
    'exact',
    'pty',
    'preserve-env',
    'test-only',
    'use-min-nodes',
    'wait-all-nodes',
    'multi-prog',
    'requeue',
    'no-requeue',
    'hold',
    'spread-job',
})

# Short option -> long option for the flags srun shares with sbatch.
_SRUN_SHORT_TO_LONG: Dict[str, str] = {
    'n': 'ntasks',
    'N': 'nodes',
    'c': 'cpus-per-task',
    'G': 'gpus',
    'o': 'output',
    'e': 'error',
    'i': 'input',
    'l': 'label',
    'J': 'job-name',
    'D': 'chdir',
    'p': 'partition',
    'A': 'account',
    't': 'time',
    'w': 'nodelist',
    'x': 'exclude',
    'r': 'relative',
    'q': 'qos',
    'd': 'dependency',
    'v': 'verbose',
    'Q': 'quiet',
    'm': 'distribution',
    'Z': 'no-allocate',
    'X': 'disable-status',
    'K': 'kill-on-bad-exit',
    'O': 'overcommit',
    's': 'oversubscribe',
    'T': 'threads',
    'W': 'wait',
}


def _parse_srun_flags(tokens: List[str]) -> Tuple[Dict[str, str], List[str]]:
    """Split ``srun``'s tokens into a dict of flags and the command tokens.

    Returns ``(flags, command_tokens)``. Flags without a value get the empty
    string. Stops at the first positional argument or ``--`` terminator.

    Boolean flags listed in ``_SRUN_BOOLEAN_FLAGS`` are recognised as taking
    no value, so ``srun -l python x.py`` parses ``python`` as the command
    rather than as the value of ``--label``.
    """
    flags: Dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == '--':
            i += 1
            break
        if tok.startswith('--'):
            body = tok[2:]
            if '=' in body:
                key, value = body.split('=', 1)
                flags[key] = value
                i += 1
            elif body in _SRUN_BOOLEAN_FLAGS:
                flags[body] = ''
                i += 1
            elif (i + 1 < len(tokens) and not tokens[i + 1].startswith('-')):
                flags[body] = tokens[i + 1]
                i += 2
            else:
                flags[body] = ''
                i += 1
        elif tok.startswith('-') and len(tok) >= 2:
            short = tok[1]
            rest = tok[2:]
            key = _SRUN_SHORT_TO_LONG.get(short, short)
            if rest:
                flags[key] = rest
                i += 1
            elif key in _SRUN_BOOLEAN_FLAGS:
                flags[key] = ''
                i += 1
            elif (i + 1 < len(tokens) and not tokens[i + 1].startswith('-')):
                flags[key] = tokens[i + 1]
                i += 2
            else:
                flags[key] = ''
                i += 1
        else:
            break
    return flags, tokens[i:]


def _translate_srun_line(
        indent: str, stripped: str,
        num_nodes: Optional[int]) -> Tuple[List[str], List[str]]:
    """Translate a single ``srun ...`` line.

    Returns ``(new_lines, warnings)``. ``new_lines`` is the list of output
    lines that should replace the original line (already indented).
    """
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return [indent + stripped], []
    assert tokens and tokens[0] == 'srun', tokens
    flags, command_tokens = _parse_srun_flags(tokens[1:])

    if not command_tokens:
        # ``srun`` with no positional command -- nothing useful we can do.
        return [indent + stripped], [
            f'Could not find a command after `srun` in line: {stripped!r}.'
        ]

    command = ' '.join(_shell_safe_quote(t) for t in command_tokens)
    warnings: List[str] = []

    # Figure out how many tasks per node this srun asks for.
    def _as_int(v: Optional[str]) -> Optional[int]:
        if v is None or v == '':
            return None
        try:
            return int(v)
        except ValueError:
            return None

    tasks_per_node = _as_int(flags.get('ntasks-per-node'))
    total_tasks = _as_int(flags.get('ntasks'))
    srun_nodes = _as_int(flags.get('nodes'))

    # Single-task invocation in an otherwise multi-node job -- gate on rank 0.
    # Slurm semantics: ``--ntasks-per-node=1`` alone means "one task per node"
    # which still runs on every node; only explicit ``--nodes=1`` or
    # ``--ntasks=1`` (without a per-node multiplier) forces a single node.
    is_single_task = False
    if srun_nodes == 1 and (tasks_per_node is None or tasks_per_node == 1):
        is_single_task = True
    elif (total_tasks == 1 and srun_nodes is None and tasks_per_node is None):
        is_single_task = True

    # Detect unrecognized flags (i.e. ones that we cannot safely drop).
    handled = {'ntasks', 'nodes', 'ntasks-per-node'}
    unknown = sorted(
        k for k in flags if k not in _SRUN_FLAGS_TO_DROP and k not in handled)
    if unknown:
        warnings.append(
            f'Dropped unrecognized srun flag(s) {unknown} from line: '
            f'{stripped!r}. Please verify the translated command.')

    if is_single_task and num_nodes and num_nodes > 1:
        # Only run on the head node; Slurm's -N1 -n1 semantics.
        return ([
            indent + 'if [ "${SKYPILOT_NODE_RANK:-0}" = "0" ]; then',
            indent + '  ' + command,
            indent + 'fi',
        ], warnings)

    if tasks_per_node is not None and tasks_per_node > 1:
        warnings.append(
            f'`srun --ntasks-per-node={tasks_per_node}` needs a distributed '
            'launcher; commented `torchrun` and `mpirun` templates were '
            'emitted next to the command in the generated YAML.')
        # Emit concrete launcher templates next to the command so the user
        # has a copy-pasteable starting point.
        template: List[str] = [
            indent + f'# TODO: `srun --ntasks-per-node={tasks_per_node}` has',
            indent + '# no direct SkyPilot equivalent. Replace the command',
            indent + '# below with one of these launchers:',
            indent + '#',
            indent + '# PyTorch DDP (torchrun):',
            indent + '#   MASTER_ADDR=$(echo "$SKYPILOT_NODE_IPS" | head -n1)',
            indent + '#   torchrun \\',
            indent + '#     --nnodes=$SKYPILOT_NUM_NODES \\',
            indent + f'#     --nproc_per_node={tasks_per_node} \\',
            indent + '#     --node_rank=$SKYPILOT_NODE_RANK \\',
            indent + '#     --master_addr=$MASTER_ADDR --master_port=29500 \\',
            indent + '#     ' + command,
            indent + '#',
            indent + '# MPI (run once, on the head node only):',
            indent + '#   if [ "${SKYPILOT_NODE_RANK:-0}" = "0" ]; then',
            indent + '#     echo "$SKYPILOT_NODE_IPS" > /tmp/hostfile',
            indent + f'#     mpirun -np $(($SKYPILOT_NUM_NODES * '
            f'{tasks_per_node})) \\',
            indent + '#       --hostfile /tmp/hostfile \\',
            indent + f'#       --map-by ppr:{tasks_per_node}:node \\',
            indent + '#       ' + command,
            indent + '#   fi',
            indent + command,
        ]
        return template, warnings
    elif (total_tasks is not None and num_nodes and num_nodes > 1 and
          total_tasks != num_nodes):
        warnings.append(
            f'`srun --ntasks={total_tasks}` with {num_nodes} nodes in line '
            f'{stripped!r}: SkyPilot runs the `run:` block on every node '
            f'({num_nodes} tasks total). Adjust if you need a different '
            'layout.')

    return [indent + command], warnings


def _translate_mpi_launcher_line(indent: str,
                                 stripped: str) -> Tuple[List[str], List[str]]:
    """Translate a leading ``mpirun``/``mpiexec`` invocation.

    SkyPilot does not manage the MPI universe for you, but the hostfile
    should come from ``$SKYPILOT_NODE_IPS`` and the launcher should only run
    on the head node. We prepend a commented snippet showing the typical
    setup and leave the original command intact for the user to edit.
    """
    template = [
        indent + '# TODO: mpirun/mpiexec needs a hostfile from SkyPilot and',
        indent + '# should only be launched from the head node. Typical setup:',
        indent + '#   if [ "${SKYPILOT_NODE_RANK:-0}" = "0" ]; then',
        indent + '#     echo "$SKYPILOT_NODE_IPS" > /tmp/hostfile',
        indent + '#     ' + stripped + ' --hostfile /tmp/hostfile',
        indent + '#   fi',
        indent + stripped,
    ]
    return template, [
        f'Left MPI launcher unchanged on line: {stripped!r}. See the '
        'commented template in the generated YAML for the hostfile and '
        'head-node-only setup.'
    ]


# Slurm-only commands that won't work outside a Slurm allocation.
_SLURM_ONLY_COMMAND_RE = re.compile(
    r'(^|[\s;&|`(])(sbcast|sgather|sattach|squeue|sacct|sinfo|scontrol|'
    r'salloc|srun_cr|smap|sshare|sprio|sstat|sreport|sdiag|sbatch|scancel)'
    r'\b')

# Heuristics for commands that *work* in a SkyPilot ``run:`` block but really
# belong in ``setup:`` so they don't re-run on every launch.
_SETUP_HINT_RE = re.compile(
    r'(^|[\s;&|`(])(pip\s+install|pip3\s+install|conda\s+(create|install)|'
    r'apt(-get)?\s+install|yum\s+install|dnf\s+install|brew\s+install|'
    r'mamba\s+install|micromamba\s+install)\b')

# ``module load``/``module purge`` won't work without environment modules
# installed, which SkyPilot images normally don't have.
_MODULE_RE = re.compile(r'(^|[\s;&|`(])module\s+(load|purge|swap|unload|use)\b')


def _translate_body(body: str,
                    num_nodes: Optional[int]) -> Tuple[str, List[str]]:
    """Rewrite the script body: translate ``srun``/``mpirun`` invocations."""
    warnings: List[str] = []
    saw_module_load = False
    saw_setup_hint = False
    saw_slurm_only = False
    saw_inline_srun = False
    out_lines: List[str] = []
    # Collapse ``\\``-continued lines so multi-line ``srun`` invocations are
    # seen as a single line by the per-line translator.
    lines = _join_continuations(body.splitlines())
    for line in lines:
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]
        # Body-level diagnostics (don't transform; just warn once each).
        if not saw_module_load and _MODULE_RE.search(stripped):
            saw_module_load = True
        if not saw_setup_hint and _SETUP_HINT_RE.search(stripped):
            saw_setup_hint = True
        if not saw_slurm_only and _SLURM_ONLY_COMMAND_RE.search(stripped):
            saw_slurm_only = True
        # Detect ``srun`` that isn't at the start of a line, e.g.
        # ``time srun ...``, ``cd /tmp && srun ...``, ``(srun ...)``.
        if (not saw_inline_srun and 'srun' in stripped and
                not (stripped == 'srun' or stripped.startswith('srun '))):
            if re.search(r'(^|[\s;&|`(])srun\s', stripped):
                saw_inline_srun = True

        if stripped == 'srun' or stripped.startswith('srun '):
            new_lines, ws = _translate_srun_line(indent, stripped, num_nodes)
            out_lines.extend(new_lines)
            warnings.extend(ws)
        elif (stripped.startswith('mpirun ') or
              stripped.startswith('mpiexec ') or stripped == 'mpirun' or
              stripped == 'mpiexec'):
            new_lines, ws = _translate_mpi_launcher_line(indent, stripped)
            out_lines.extend(new_lines)
            warnings.extend(ws)
        else:
            out_lines.append(line)

    if saw_module_load:
        warnings.append(
            'Found `module load` (or similar) in the script body. Slurm '
            'environment modules are not available in SkyPilot tasks; '
            'use a Docker `image_id` or install dependencies in `setup:` '
            'instead.')
    if saw_setup_hint:
        warnings.append(
            'Found `pip/conda/apt install` (or similar) in the script body. '
            'Move these into a `setup:` block so they only run once per '
            'cluster launch instead of every job submission.')
    if saw_slurm_only:
        warnings.append(
            'Found Slurm-only commands (sbcast/sgather/sattach/squeue/...) '
            'in the script body. These will not work in a SkyPilot task; '
            'replace them with the SkyPilot equivalent (see the migration '
            'guide).')
    if saw_inline_srun:
        warnings.append(
            'Found `srun` not at the start of a line (e.g. `time srun ...`, '
            '`cmd && srun ...`, `(srun ...)`). The converter only translates '
            'leading `srun` invocations; please review these lines manually.')

    return '\n'.join(out_lines), warnings


def _yaml_scalar(value: str) -> str:
    """Render ``value`` as a safe YAML scalar.

    Values that are ambiguous or contain YAML special characters are wrapped
    in single quotes; simple strings are emitted unquoted.
    """
    if not value:
        return "''"
    needs_quoting = (any(c in value for c in ':#&*!|>\'"%@`,[]{}') or
                     value[0] in ' -?' or value != value.strip() or
                     value.lower()
                     in {'true', 'false', 'yes', 'no', 'null', '~'})
    if needs_quoting:
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return value


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
        # Slurm also allows ``min-max``; pick the minimum and warn the user.
        if '-' in nodes_val:
            min_part, max_part = nodes_val.split('-', 1)
            warnings.append(
                f'--nodes={nodes_val} is a range; SkyPilot needs a fixed '
                f'count. Using {min_part}; change `num_nodes` if you want '
                f'{max_part} instead.')
            nodes_val = min_part
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
        # Slurm treats ``--mem=0`` as "give me all the memory on the node";
        # there's no SkyPilot equivalent, so skip and warn.
        if mem_val.strip().rstrip('BbKkMmGgTt') == '0':
            warnings.append(
                '--mem=0 means "all memory" in Slurm; SkyPilot has no '
                'equivalent. Skipping memory constraint.')
        else:
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

    # ``--time``: Slurm's wall-clock limit translates to SkyPilot's autostop,
    # which tears the cluster down once idle (and so once the job finishes).
    # See
    # https://docs.skypilot.co/en/latest/reference/slurm/slurm-getting-started.html
    autostop_minutes: Optional[int] = None
    time_val = directives.pop('time', None)
    if time_val is not None:
        autostop_minutes = _parse_slurm_time(time_val)
        if autostop_minutes is None:
            warnings.append(
                f'Could not parse --time={time_val!r}; skipping autostop.')

    # ``--partition``: a Slurm partition maps to SkyPilot's
    # ``infra: slurm/<cluster>/<partition>`` selector. We don't know the
    # cluster name from the script, so leave a ``<cluster>`` placeholder for
    # the user to fill in.
    infra_value: Optional[str] = None
    partition_val = directives.pop('partition', None)
    if partition_val:
        infra_value = f'slurm/<cluster>/{partition_val}'
        warnings.append(
            f'--partition={partition_val} was mapped to '
            f'`resources.infra: {infra_value}`. Replace `<cluster>` with '
            'the name of your configured Slurm cluster from '
            '`~/.sky/config.yaml`.')

    # ``--container-image`` (Pyxis / enroot, common on HPC clusters) maps to
    # SkyPilot's ``image_id`` -- prefix with ``docker:`` if the user didn't.
    image_id: Optional[str] = None
    container_image = directives.pop('container-image', None)
    if container_image:
        if '://' in container_image:
            # E.g. ``docker://nvidia/cuda:12.1.1`` -- normalize to docker:.
            scheme, rest = container_image.split('://', 1)
            image_id = f'{scheme}:{rest}' if scheme == 'docker' \
                else container_image
        elif container_image.startswith('docker:') or \
                container_image.startswith('oci:'):
            image_id = container_image
        else:
            image_id = f'docker:{container_image}'
    # Other Pyxis ``--container-*`` options aren't first-class in SkyPilot.
    # Drop them with a heads-up so they don't get pushed into sbatch_options
    # where they'd no-op.
    for pyxis_key in ('container-mounts', 'container-workdir', 'container-name',
                      'container-save', 'container-env', 'container-remap-root',
                      'container-writable', 'container-readonly'):
        if pyxis_key in directives:
            value = directives.pop(pyxis_key)
            warnings.append(
                f'Dropped Pyxis option --{pyxis_key}={value}: SkyPilot '
                'manages container mounts and entry differently. Use '
                '`file_mounts:` and `setup:` instead.')

    # ``--ntasks=N`` is only meaningful inside a Slurm allocation; SkyPilot
    # always launches the ``run`` block once per node. Drop it with a note.
    if 'ntasks' in directives:
        ntasks_val = directives.pop('ntasks')
        warnings.append(
            f'Dropped --ntasks={ntasks_val}: SkyPilot launches `run:` once '
            'per node. Use a distributed launcher (torchrun/mpirun) inside '
            'the run block to spawn additional tasks.')

    # Directives that SkyPilot does not map directly.
    unsupported_notes: List[Tuple[str, str]] = []

    def _note(flag: str, msg: str) -> None:
        value = directives.pop(flag, None)
        if value is None:
            return
        label = f'--{flag}={value}' if value else f'--{flag}'
        unsupported_notes.append((label, msg))

    # These directives are managed by SkyPilot itself or don't make sense as
    # per-task sbatch options. Kept as review comments instead of being passed
    # through as ``config.slurm.sbatch_options``.
    _note(
        'output', 'SkyPilot captures stdout/stderr automatically; view with '
        '`sky logs` or `sky jobs logs`.')
    _note('error',
          'SkyPilot captures stderr automatically; view with `sky logs`.')
    _note(
        'array', 'Slurm job arrays have no direct equivalent. Launch with a '
        'shell loop over `sky jobs launch --env TASK_ID=$i ...`.')

    # The sbatch options that the Slurm provisioner controls. Passing these
    # through ``sbatch_options`` just triggers a warning at provision time, so
    # we skip them here. Mirrors ``_SBATCH_PROTECTED_OPTIONS`` in
    # sky/provision/slurm/instance.py.
    _PROTECTED = {
        'job-name',
        'output',
        'error',
        'nodes',
        'time',
        'wait-all-nodes',
        'no-requeue',
        'cpus-per-task',
        'mem',
        'gres',
        'partition',
    }

    # Any remaining directive that isn't protected is a valid per-task sbatch
    # option and can be pushed into ``config.slurm.sbatch_options`` to round-
    # trip faithfully through the Slurm backend.
    # https://docs.skypilot.co/en/latest/reference/slurm/slurm-getting-started.html
    sbatch_passthrough: Dict[str, str] = {}
    for flag, value in list(directives.items()):
        if flag in _PROTECTED:
            label = f'--{flag}={value}' if value else f'--{flag}'
            unsupported_notes.append((
                label,
                'Protected by SkyPilot; cannot be set via `config.slurm.'
                'sbatch_options`. Please review.',
            ))
            directives.pop(flag, None)
            continue
        sbatch_passthrough[flag] = value
        directives.pop(flag, None)

    # Body handling: substitute env vars and translate srun/mpirun lines.
    body = '\n'.join(parsed.body_lines).strip('\n')
    body = _substitute_env_vars(body)
    body, body_warnings = _translate_body(body, num_nodes)
    warnings.extend(body_warnings)

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
    if infra_value:
        resources.append(f'  infra: {infra_value}')
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
    if image_id:
        resources.append(f'  image_id: {_yaml_scalar(image_id)}')
    if resources:
        lines.append('resources:')
        lines.extend(resources)
        lines.append('')

    if autostop_minutes is not None:
        lines.append('autostop:')
        lines.append(f'  idle_minutes: {autostop_minutes}')
        lines.append('  down: true')
        lines.append('  wait_for: none')
        lines.append('')

    if sbatch_passthrough:
        lines.append('config:')
        lines.append('  slurm:')
        lines.append('    sbatch_options:')
        for flag in sorted(sbatch_passthrough):
            value = sbatch_passthrough[flag]
            if value == '':
                # Flag with no value -- emit as a boolean true.
                lines.append(f'      {flag}: true')
            else:
                # Quote strings to keep YAML happy for values with
                # characters like ``:`` (e.g. ``--mail-type=END:FAIL``).
                lines.append(f'      {flag}: {_yaml_scalar(value)}')
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
