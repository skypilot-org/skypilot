"""Why a Slurm job is PENDING: reason codes turned into an answer.

``squeue %R`` / ``scontrol Reason=`` give one opaque code -- ``Resources``,
``QOSGrpGRES``, ``DependencyNeverSatisfied``. ``classify_pending`` maps it onto
one of five categories (``quota``, ``resources``, ``held``, ``dependency``,
``other``) and writes a plain-text summary plus, where there is enough to say
one, an action.

Everything here is pure: no I/O, no scheduler calls. The caller gathers
whatever evidence it can and passes it in through ``PendingEvidence``, which
is what lets very different callers share one classification. Nothing is
guessed -- with the evidence for a claim missing, the summary says so and
``action`` is ``None`` rather than a plausible-sounding remedy.

That split is also where the module boundary is. Answering about *your own*
job needs this job's reason, its partition's node counts
(``utils.slurm_node_info``) and the states of the jobs it depends on -- all
reachable with the access that submitted the job. Naming the *cap* behind a
quota reason instead needs the accounting database's QoS definitions, and
deciding which QoS a partition attaches or which GRES a cap refers to are
rules about that database; a caller that reads it resolves them and passes
the results in as ``effective_qos`` / ``blocking_resource``.
"""

import dataclasses
import datetime
import re
from typing import Any, Dict, List, Optional, Set

# squeue renders a PENDING job's reason through ``%R``, which wraps it in
# parentheses ('(QOSGrpGRES)'); ``%r`` and ``scontrol`` give the bare code.
# Both spellings normalize here, and the frontend applies the same rule
# before rendering, so neither side has to know which one produced the row.
_PARENTHESIZED_RE = re.compile(r'^\((.*)\)$')

# Slurm's spellings for "no reason yet". The sweep filters the bare 'None'
# only, so the parenthesized form reaches this module as a reason.
_NO_REASON = frozenset({'', 'NONE', 'NULL'})

# The reason codes this module rewrites into "what the job is short of".
# QOSGrpGRES is the QOS's *group* GRES cap (GrpTRES gres/...) being full --
# the one pending reason that names a resource without naming *which*, and
# the only shape a user can act on ("my team is out of B300", not "there is
# a limit called QOSGrpGRES"). Its per-user and per-job siblings
# (QOSMaxGRESPerUser, QOSMaxGRESPerJob) resolve against a different cap and
# are deliberately not covered yet.
GRES_LIMIT_REASONS = frozenset({'QOSGrpGRES'})

# What an untyped ``gres/gpu`` cap is called when the job's own model is
# unknowable. Plural: it names the resource, not one accelerator.
_GENERIC_GPU_LABEL = 'GPUs'


def pending_reason_code(job: Dict[str, Any]) -> str:
    """The bare Slurm reason code of a PENDING job, or ''.

    '' for a job that is not pending, carries no reason, or carries one of
    Slurm's "none" spellings -- so a caller can test the code directly
    instead of re-deriving the "is there a reason at all" question.
    """
    if (job.get('state') or '').upper() != 'PENDING':
        return ''
    text = str(job.get('reason') or '').strip()
    match = _PARENTHESIZED_RE.match(text)
    if match:
        text = match.group(1).strip()
    return '' if text.upper() in _NO_REASON else text


DEPENDENCY_REASONS = frozenset({'Dependency', 'DependencyNeverSatisfied'})
_UNSATISFIABLE_REASON = 'DependencyNeverSatisfied'

# One entry of squeue's ``%E``. Slurm builds these from the job's dependency
# list (``_foreach_depend_list2str``): a type, the job it names -- optionally
# an array task (``123_4``, ``123_*``) and a ``+minutes`` offset -- and the
# entry's own state in parentheses. Entries are joined by ',' for AND and '?'
# for OR, and a *fulfilled* entry is dropped from the string entirely, so what
# arrives here is only what is still outstanding. ``singleton`` names no job
# at all.
#
# One entry can name several jobs (``afterok:123:124``): Slurm's own renderer
# splits those into separate entries, but the man page says a dependency that
# can never be satisfied is reported as *the full original specification* --
# which is whatever the user typed.
_DEPENDENCY_STATE_RE = re.compile(r'\((?P<state>[A-Za-z]+)\)$')
_DEPENDENCY_TIME_RE = re.compile(r'\+\d+')
_DEPENDENCY_JOB_RE = re.compile(r'\d+(?:_(?:\d+|\*))?')

# The entry state that will never become 'fulfilled'.
_FAILED_STATE = 'failed'


def parse_dependency(expression: str) -> Dict[str, Any]:
    """``'afterok:5122(unfulfilled)'`` -> the jobs it is waiting on.

    Returns ``{'job_ids': [...], 'failed_job_ids': [...], 'singleton': bool,
    'unsatisfiable': bool}``.

    ``singleton`` is its own key because it names no job: it waits on any
    earlier job of the same name and user, which is a different sentence for
    the UI to say.

    ``unsatisfiable`` means the job will pend forever, which is worth saying
    differently from an ordinary wait. Which ``(failed)`` entries make it so
    depends on the separator: sbatch joins a dependency list with ``,`` (all
    must be satisfied) or with ``?`` (any one is enough), never a mix. A
    failed entry is therefore fatal under ``,``, but under ``?`` the job still
    runs while one alternative survives.

    An entry this cannot parse is skipped rather than guessed at; an
    expression that yields nothing at all leaves the UI on the raw reason
    code. Slurm builds this string itself, so the shapes are fixed, but the
    annotations are recent enough that an older cluster may send bare
    entries -- which parse fine and simply carry no state.
    """
    parsed: Dict[str, Any] = {
        'job_ids': [],
        'failed_job_ids': [],
        'singleton': False,
        'unsatisfiable': False,
    }
    expression = expression.strip()
    any_of = '?' in expression
    entries = 0
    failed = 0
    for raw in re.split(r'[,?]', expression):
        entry = raw.strip()
        if not entry:
            continue
        entries += 1
        state = _DEPENDENCY_STATE_RE.search(entry)
        if state:
            entry = entry[:state.start()]
        kind, _, rest = entry.partition(':')
        if not kind.isalpha():
            continue
        entry_failed = (state is not None and
                        state.group('state').lower() == _FAILED_STATE)
        if entry_failed:
            failed += 1
        if kind.lower() == 'singleton':
            parsed['singleton'] = True
            continue
        # A '+minutes' offset would otherwise read as a job id.
        for job_id in _DEPENDENCY_JOB_RE.findall(
                _DEPENDENCY_TIME_RE.sub('', rest)):
            if job_id not in parsed['job_ids']:
                parsed['job_ids'].append(job_id)
            if entry_failed and job_id not in parsed['failed_job_ids']:
                parsed['failed_job_ids'].append(job_id)
    # An entry too odd to parse still counts in ``entries``, so it keeps an
    # any-of list off the unsatisfiable verdict rather than being read as one
    # more closed path.
    if failed and (failed == entries or not any_of):
        parsed['unsatisfiable'] = True
    return parsed


# Slurm's non-epoch timestamp form, e.g. '2026-09-08T04:05:47'.
_ISO_TIME_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})$')

CATEGORY_QUOTA = 'quota'
CATEGORY_RESOURCES = 'resources'
CATEGORY_HELD = 'held'
CATEGORY_DEPENDENCY = 'dependency'
CATEGORY_OTHER = 'other'

# Matched against the reason's first comma-separated token with whitespace
# folded to '_' (Slurm spells some reasons as sentences: 'Nodes required for
# job are DOWN, DRAINED or reserved ...', 'launch failed requeued held').
_QUOTA_PREFIXES = ('QOSGrp', 'QOSMax', 'QOSMin', 'AssocGrp', 'AssocMax')
_QUOTA_CODES = frozenset({'QOSUsageThreshold'})
_RESOURCE_CODES = frozenset({
    'Resources', 'Priority', 'ReqNodeNotAvail', 'PartitionDown', 'Reservation'
})
_NODES_DOWN_PREFIX = 'Nodes_required_for_job_are_DOWN'
_HELD_CODES = frozenset({
    'JobHeldUser',
    'JobHeldAdmin',
    'JobHoldMaxRequeue',
    'launch_failed_requeued_held',
    'BeginTime',
})

# ---------------------------------------------------------------------------
# Evidence shaping: pure functions over what a sinfo / squeue read returned.
# ---------------------------------------------------------------------------


def partitions_of(value: Any) -> Set[str]:
    return {
        p.strip().rstrip('*') for p in str(value or '').split(',') if p.strip()
    }


# sinfo base states (flags stripped) that mean the node cannot take work.
_DRAIN_STATES = ('drain', 'drng', 'draining', 'drained')
_DOWN_STATES = ('down', 'fail', 'failg', 'failing', 'error', 'inval', 'invalid',
                'unk')
_BUSY_STATES = ('alloc', 'allocated', 'mix', 'mixed', 'comp', 'completing')
# The state sinfo reports for a node that is up with nothing running on it.
# Only this counts as idle; everything else is unavailable, including a state
# this code has never seen. That bias is deliberate: the resources summary
# makes a claim about the idle count specifically, so an unrecognized spelling
# must never inflate it. What lands in unavailable today: resv, maint, plnd,
# futr, npc, perf, pow_up -- present, but nothing a caller submits can go
# there.
_IDLE_STATES = ('idle',)
_STATE_FLAGS = '*~#%!$@^-+'


def partition_node_counts(
    node_infos: List[Dict[str, Any]],
    partitions: Set[str],
    busy_nodes: Set[str],
) -> Optional[Dict[str, int]]:
    """total/idle/allocated/drained/down/unavailable/powered_down over the
    nodes of ``partitions``.

    ``slurm_node_info`` queries sinfo/scontrol live (the 30-minute kv cache in
    that module belongs to ``get_slurm_nodes_info``, a different function), so
    every state here is current. ``busy_nodes`` comes from the running jobs in
    the sweep and is only an overlay: sinfo reports a mixed-allocation node as
    idle, and the sweep is at most one window old. None when no node belongs
    to the partitions. ``powered_down`` is counted apart from ``total``,
    which is the fleet that is actually present."""
    counts = {
        'total': 0,
        'idle': 0,
        'allocated': 0,
        'drained': 0,
        'down': 0,
        'unavailable': 0,
        'powered_down': 0,
    }
    for info in node_infos:
        if not partitions_of(info.get('partition')).intersection(partitions):
            continue
        raw = str(info.get('node_state') or '')
        base = raw.rstrip(_STATE_FLAGS).lower()
        # '~' is the flag form and 'pow_dn' the base state; both mean the node
        # is off and has to be resumed before it can run anything.
        if '~' in raw or base.startswith('pow_dn'):
            counts['powered_down'] += 1
            continue
        counts['total'] += 1
        name = str(info.get('node_name') or '')
        if base.startswith(_DRAIN_STATES):
            counts['drained'] += 1
        elif '*' in raw or base.startswith(_DOWN_STATES):
            counts['down'] += 1
        elif name in busy_nodes or base.startswith(_BUSY_STATES):
            counts['allocated'] += 1
        elif base.startswith(_IDLE_STATES):
            counts['idle'] += 1
        else:
            counts['unavailable'] += 1
    return counts if counts['total'] or counts['powered_down'] else None


def pending_ahead(sweep: List[Dict[str, Any]], job: Dict[str,
                                                         Any]) -> Optional[int]:
    """Pending jobs sharing a candidate partition that Slurm will consider
    before this one. None when this job's priority is unknown.

    Higher priority (squeue %Q) goes first, and **a tie is broken by job id**.
    Counting only strictly higher priorities looks equivalent until you meet a
    cluster with no multifactor priority configured: there every job reports
    priority 1, every comparison ties, and the answer is always "nothing is
    ahead of you" while an older job is plainly next in line. Slurm breaks
    such ties by submission, which the id orders -- so an id lower than
    ours is ahead, and a job array element's `17221_2` compares by its base
    number.
    """
    mine = _int_or_none(job.get('priority'))
    if mine is None:
        return None
    my_id = _base_job_id(job.get('job_id'))
    partitions = partitions_of(job.get('partition'))
    ahead = 0
    for other in sweep:
        if str(other.get('job_id')) == str(job.get('job_id')):
            continue
        if (other.get('state') or '').upper() != 'PENDING':
            continue
        if not partitions_of(other.get('partition')).intersection(partitions):
            continue
        theirs = _int_or_none(other.get('priority'))
        if theirs is None:
            continue
        if theirs > mine:
            ahead += 1
        elif theirs == mine and my_id is not None:
            their_id = _base_job_id(other.get('job_id'))
            if their_id is not None and their_id < my_id:
                ahead += 1
    return ahead


def _base_job_id(value: Any) -> Optional[int]:
    """The numeric part of a Slurm job id, for ordering by submission.

    Slurm spells an array element `17221_2` and a heterogeneous component
    `123+0`; both share the base job's submission order, which is all this is
    used for.
    """
    if value is None:
        return None
    head = re.split(r'[_+]', str(value).strip(), maxsplit=1)[0]
    try:
        return int(head)
    except ValueError:
        return None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _reason_key(code: str) -> str:
    """The token a reason is matched on: first comma field, spaces as '_'."""
    head = code.split(',', 1)[0].strip()
    return re.sub(r'\s+', '_', head)


def pending_category(code: str) -> str:
    """One of the five taxonomy categories for a bare reason code."""
    key = _reason_key(code)
    if not key:
        return CATEGORY_OTHER
    if key in _QUOTA_CODES or key.startswith(_QUOTA_PREFIXES):
        return CATEGORY_QUOTA
    if key in _RESOURCE_CODES or key.startswith(_NODES_DOWN_PREFIX):
        return CATEGORY_RESOURCES
    if key in _HELD_CODES:
        return CATEGORY_HELD
    if key in DEPENDENCY_REASONS:
        return CATEGORY_DEPENDENCY
    return CATEGORY_OTHER


@dataclasses.dataclass
class PendingEvidence:
    """What the caller managed to read for a pending job. Every field is
    optional; ``None`` means "not read", not "zero"."""

    # scontrol ``Restarts=``.
    restarts: Optional[int] = None
    # scontrol ``StartTime=`` for a BeginTime hold (epoch seconds or raw).
    begin_time: Optional[str] = None
    # sacctmgr QOS definitions (accounting.parse_qos_output). None when the
    # read was not attempted; {} with ``accounting_error`` when it failed.
    qoses: Optional[Dict[str, Dict[str, Any]]] = None
    accounting_error: Optional[str] = None
    # scontrol partition definitions (accounting.parse_partitions_output).
    partitions: Optional[Dict[str, Dict[str, Any]]] = None
    # accounting.rollup_usage output: live GPUs per QOS.
    qos_usage: Optional[Dict[str, Dict[str, int]]] = None
    # The QoS name(s) this job counts against, and what the blocking cap is
    # called ('H200'). Both are resolved by the caller: deciding which QoS a
    # partition attaches, and which GRES a cap refers to, needs the accounting
    # database's definitions, so the caller that reads them owns those rules.
    # None means nobody resolved them -- the quota branch then says the cap is
    # unknown instead of naming one.
    effective_qos: Optional[List[str]] = None
    blocking_resource: Optional[str] = None
    # Node counts for the job's partition(s): total/idle/allocated/drained/
    # down/powered_down.
    partition_nodes: Optional[Dict[str, int]] = None
    # Pending jobs in the same partition(s) with a higher priority.
    pending_ahead: Optional[int] = None
    # Live state of every job in the queue, for dependency lookups. A job
    # missing from this map is no longer queued.
    dependency_states: Optional[Dict[str, str]] = None


def _text(value: str) -> str:
    return ' '.join(str(value).split())


def _result(
    category: str,
    code: str,
    summary: str,
    action: Optional[str],
    evidence: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        'category': category,
        'code': code,
        'summary': _text(summary),
        'action': _text(action) if action else None,
        'evidence': evidence,
    }


def classify_pending(
    job: Dict[str, Any],
    evidence: Optional[PendingEvidence] = None,
) -> Optional[Dict[str, Any]]:
    """``{category, code, summary, action, evidence}`` for a PENDING job.

    ``job`` is a squeue-shaped row (state, reason, dependency, partition,
    qos, gpu_type, job_id). Returns None for a job that is not pending or
    carries no reason.
    """
    code = pending_reason_code(job)
    if not code:
        return None
    evidence = evidence or PendingEvidence()
    category = pending_category(code)
    if category == CATEGORY_QUOTA:
        return _classify_quota(job, code, evidence)
    if category == CATEGORY_RESOURCES:
        return _classify_resources(job, code, evidence)
    if category == CATEGORY_HELD:
        return _classify_held(job, code, evidence)
    if category == CATEGORY_DEPENDENCY:
        return _classify_dependency(job, code, evidence)
    return _result(CATEGORY_OTHER, code, code, None, {})


def _plural(count: int, noun: str) -> str:
    return f'{count} {noun}' + ('' if count == 1 else 's')


def _classify_quota(job: Dict[str, Any], code: str,
                    evidence: PendingEvidence) -> Dict[str, Any]:
    # Uniform signature with the other classifiers; the job's own fields are
    # not consulted here, since which QoS it counts against and which resource
    # the cap names both arrive resolved.
    del job
    key = _reason_key(code)
    queues = evidence.effective_qos or []
    found: Dict[str, Any] = {'qos': queues or None}
    if evidence.qoses is None or (not evidence.qoses and
                                  evidence.accounting_error):
        found['accounting_error'] = evidence.accounting_error
        why = (f' ({evidence.accounting_error})'
               if evidence.accounting_error else '')
        return _result(
            CATEGORY_QUOTA,
            code,
            f'Slurm reports a QoS/association limit ({code}). The accounting '
            f'database could not be read{why}, so the cap behind it is '
            f'unknown.',
            None,
            found,
        )
    if len(queues) != 1:
        # A multi-partition pending job counts against several QoS; without
        # knowing which one Slurm charged the limit to, no cap can be named.
        names = ', '.join(queues) if queues else 'unknown'
        return _result(
            CATEGORY_QUOTA,
            code,
            f'A QoS/association limit ({code}) is blocking this job; '
            f'candidate QoS: {names}. The specific cap could not be resolved.',
            None,
            found,
        )
    qos_name = queues[0]
    qos = evidence.qoses.get(qos_name) or {}
    label = evidence.blocking_resource or ''
    nominal = qos.get('nominal') or {}
    gres_caps = {k: v for k, v in nominal.items() if k.startswith('gres/')}
    cap_key, cap = (next(iter(gres_caps.items())) if len(gres_caps) == 1 else
                    (None, None))
    # Only pair a cap with usage read under the SAME TRES key. The cap may be
    # typed (``gres/gpu:a100``) or not a GPU at all, while rollup_usage keeps
    # only the untyped ``gres/gpu`` total -- pairing those printed ratios like
    # 32/16. A key with no matching usage falls through to the cap-only text.
    used = ((evidence.qos_usage or {}).get(qos_name, {}).get(cap_key)
            if cap_key else None)
    found.update({
        'cap': cap,
        'cap_tres': cap_key,
        'used': used,
        'resource': label or None
    })

    if key == 'QOSGrpGRES':
        what = label or _GENERIC_GPU_LABEL
        if cap is not None and used is not None:
            summary = f'{what} quota for QoS {qos_name} is full ({used}/{cap}).'
        elif cap is not None:
            summary = f'{what} quota for QoS {qos_name} is full (cap {cap}).'
        else:
            summary = f'{what} group quota for QoS {qos_name} is full.'
        action = (
            f'Wait for the group\'s jobs in QoS {qos_name} to finish, '
            f'submit to '
            f'another partition or QoS, or request a higher-priority QoS.')
        return _result(CATEGORY_QUOTA, code, summary, action, found)
    if key.startswith('QOSMax') and key.endswith('PerUser'):
        per_user = qos.get('max_tres_per_user') or {}
        found['max_tres_per_user'] = per_user or None
        cap_text = (' (' + ', '.join(f'{k}={v}' for k, v in per_user.items()) +
                    ')' if per_user else '')
        return _result(
            CATEGORY_QUOTA,
            code,
            f'Per-user limit {key} on QoS {qos_name} reached{cap_text}.',
            f'Wait for your own jobs in QoS {qos_name} to finish, or submit '
            f'under another QoS.',
            found,
        )
    if key.startswith('QOSMax') and key.endswith('PerJob'):
        return _result(
            CATEGORY_QUOTA,
            code,
            f'This job asks for more than QoS {qos_name} allows per job '
            f'({key}).',
            'Resubmit with a smaller request, or under another QoS.',
            found,
        )
    if key.startswith('Assoc'):
        return _result(
            CATEGORY_QUOTA,
            code,
            f'An association (account) limit {key} is blocking this job '
            f'(QoS {qos_name}).',
            'Check the account limits with sacctmgr show assoc, wait for the '
            'account\'s jobs to finish, or submit under another account.',
            found,
        )
    return _result(
        CATEGORY_QUOTA,
        code,
        f'QoS limit {key} on QoS {qos_name} is blocking this job.',
        f'Check the limit with sacctmgr show qos {qos_name}, wait for jobs in '
        f'that QoS to finish, or submit under another QoS.',
        found,
    )


def _node_counts_text(nodes: Dict[str, int]) -> str:
    parts = [f'{nodes.get("allocated", 0)} allocated']
    for key, label in (
        ('idle', 'idle'),
        ('drained', 'drained'),
        ('down', 'down'),
            # Reserved / under maintenance / planned. Named separately so the
            # count reads as "present but not for you", not as spare capacity.
        ('unavailable', 'unavailable'),
        ('powered_down', 'powered down'),
    ):
        if nodes.get(key):
            parts.append(f'{nodes[key]} {label}')
    return f'{_plural(nodes.get("total", 0), "node")}: {", ".join(parts)}'


def _classify_resources(job: Dict[str, Any], code: str,
                        evidence: PendingEvidence) -> Dict[str, Any]:
    key = _reason_key(code)
    partition = (job.get('partition') or '').strip() or 'unknown'
    nodes = evidence.partition_nodes
    ahead = evidence.pending_ahead
    found: Dict[str, Any] = {
        'partition': partition,
        'partition_nodes': nodes,
        'pending_ahead': ahead,
    }
    if ahead is None:
        ahead_text = ''
    elif ahead == 0:
        # Saying "0 jobs are pending ahead of this one" right after "higher
        # priority jobs are ahead of this one" reads as a contradiction. Both
        # are true -- what is ahead of it is RUNNING, not queued -- so say
        # that, since it is the difference between waiting for a backlog and
        # waiting for a full cluster.
        ahead_text = ' No other pending job is ahead of it.'
    else:
        ahead_text = (
            f' {_plural(ahead, "job")} {"is" if ahead == 1 else "are"} pending '
            f'ahead of this one.')
    if key in ('Resources', 'Priority'):
        if nodes:
            if key == 'Priority':
                summary = (
                    f'Higher-priority jobs are ahead of this one in partition '
                    f'{partition} ({_node_counts_text(nodes)}).')
            elif nodes.get('idle'):
                summary = (
                    f'The {_plural(nodes["idle"], "idle node")} in partition '
                    f'{partition} do not satisfy this job\'s request '
                    f'({_node_counts_text(nodes)}).')
            else:
                summary = (f'No idle node in partition {partition} '
                           f'({_node_counts_text(nodes)}).')
        else:
            summary = (f'Waiting for free resources in partition '
                       f'{partition} ({key}).')
        action = ('Wait for running jobs to finish, or resubmit with a smaller '
                  'request '
                  'or to a partition with idle nodes.'
                  if nodes or ahead is not None else None)
        return _result(CATEGORY_RESOURCES, code, summary + ahead_text, action,
                       found)
    if key == 'ReqNodeNotAvail':
        unavailable = re.search(r'UnavailableNodes:\s*(\S+)', code)
        found['unavailable_nodes'] = unavailable.group(
            1) if unavailable else None
        which = f' ({unavailable.group(1)})' if unavailable else ''
        return _result(
            CATEGORY_RESOURCES,
            code,
            f'A node this job requires is unavailable{which}: down, drained or '
            f'reserved.',
            'Wait for the node to return, or resubmit without the node '
            'constraint (--nodelist / --constraint).',
            found,
        )
    if key.startswith(_NODES_DOWN_PREFIX):
        counts = f' ({_node_counts_text(nodes)})' if nodes else ''
        return _result(
            CATEGORY_RESOURCES,
            code,
            f'The nodes this job requires in partition {partition} are down, '
            f'drained or reserved for higher-priority partitions{counts}.',
            'Wait for the nodes to return, or resubmit to another partition.',
            found,
        )
    if key == 'PartitionDown':
        return _result(
            CATEGORY_RESOURCES,
            code,
            f'Partition {partition} is down and not scheduling jobs.',
            'Contact the cluster administrator; the job starts once the '
            'partition is up, or resubmit to another partition.',
            found,
        )
    # Reservation.
    return _result(
        CATEGORY_RESOURCES,
        code,
        'Waiting for the requested reservation to begin.',
        'Wait for the reservation window, or resubmit without --reservation.',
        found,
    )


def _format_epoch(value: Optional[str]) -> Optional[str]:
    """A Slurm timestamp as a readable time, or None when it is not one.

    None rather than the raw value, because the caller interpolates this into
    a sentence about when something happens. Slurm answers `Unknown` for a
    time it has not computed -- for a held job, and for an array task before
    it is split out -- and "It becomes eligible at Unknown." is worse than not
    saying it. Fields that carry the scheduler's own spelling through
    untouched are shaped elsewhere (`slurm_jobs.core.shape_job`).

    The reads ask for epoch seconds (`SLURM_TIME_FORMAT=%s`), which is what
    the first branch handles. A Slurm build old enough to ignore that answers
    in ISO form in the *cluster's* local timezone, unlabelled; that is shown
    as it came rather than converted, since nothing here knows which
    timezone it is.
    """
    if value is None:
        return None
    try:
        stamp = datetime.datetime.fromtimestamp(int(value),
                                                tz=datetime.timezone.utc)
    except (TypeError, ValueError):
        iso = _ISO_TIME_RE.match(value)
        return f'{iso.group(1)} {iso.group(2)}' if iso else None
    return stamp.strftime('%Y-%m-%d %H:%M:%S UTC')


def _classify_held(job: Dict[str, Any], code: str,
                   evidence: PendingEvidence) -> Dict[str, Any]:
    key = _reason_key(code)
    job_id = job.get('job_id') or '<job id>'
    restarts = evidence.restarts
    found: Dict[str, Any] = {'restarts': restarts}
    release = f'release it with scontrol release {job_id}'
    if key == 'launch_failed_requeued_held':
        if restarts is not None:
            summary = (f'Held after {_plural(restarts, "requeue")}: the job '
                       f'launch failed.')
        else:
            summary = 'Held: the job launch failed and the job was requeued.'
        return _result(
            CATEGORY_HELD,
            code,
            summary,
            f'Check the node and prolog logs for the failure, then {release}.',
            found,
        )
    if key == 'JobHoldMaxRequeue':
        count = (f' ({_plural(restarts, "restart")})'
                 if restarts is not None else '')
        return _result(
            CATEGORY_HELD,
            code,
            f'Held after reaching the requeue limit{count}.',
            f'Find out why the job keeps being requeued, then {release}.',
            found,
        )
    if key == 'JobHeldUser':
        note = (f' It has been requeued {_plural(restarts, "time")}.'
                if restarts else '')
        return _result(
            CATEGORY_HELD,
            code,
            f'Held by the user (Slurm also reports this after a failed '
            f'requeue).{note}',
            f'If the hold is not intended, {release}.',
            found,
        )
    if key == 'JobHeldAdmin':
        return _result(
            CATEGORY_HELD,
            code,
            'Held by an administrator.',
            'Ask a Slurm administrator why it is held; only they can '
            'release it.',
            found,
        )
    # BeginTime.
    begin = _format_epoch(evidence.begin_time)
    found['begin_time'] = begin
    when = f' It becomes eligible at {begin}.' if begin else ''
    return _result(
        CATEGORY_HELD,
        code,
        f'Not eligible yet: the requested begin time is in the future.{when}',
        f'Wait, or start it now with scontrol update JobId={job_id} '
        f'StartTime=now.',
        found,
    )


def _classify_dependency(job: Dict[str, Any], code: str,
                         evidence: PendingEvidence) -> Dict[str, Any]:
    job_id = job.get('job_id') or '<job id>'
    raw = str(job.get('dependency') or '')
    parsed = parse_dependency(raw)
    never = code == _UNSATISFIABLE_REASON or parsed['unsatisfiable']
    states = evidence.dependency_states
    blocking = []
    for dep in parsed['job_ids']:
        state = states.get(dep) if states is not None else None
        blocking.append({'job_id': dep, 'state': state})
    found = {
        'dependency': raw or None,
        'blocking_jobs': blocking,
        'failed_dependencies': parsed['failed_job_ids'] or None,
        'singleton': parsed['singleton'],
        'unsatisfiable': never,
    }

    def _name(entry: Dict[str, Any]) -> str:
        # The annotation first: a failed dependency has left the queue, so
        # without this it reads as "no longer in the queue" -- true, and the
        # least useful of the two things known about it.
        if entry['job_id'] in parsed['failed_job_ids']:
            return f'{entry["job_id"]} (failed)'
        if entry['state']:
            return f'{entry["job_id"]} ({entry["state"]})'
        if states is not None:
            return f'{entry["job_id"]} (no longer in the queue)'
        return str(entry['job_id'])

    if never:
        # Name the entries Slurm marked (failed), not every job in the
        # expression: under a ',' list the others may be waiting normally.
        which = (', '.join(parsed['failed_job_ids']) or
                 ', '.join(b['job_id'] for b in blocking) or 'another job')
        return _result(
            CATEGORY_DEPENDENCY,
            code,
            f'Depends on job {which} which failed; the dependency can never be '
            f'satisfied, so this job will never run.',
            f'Cancel and resubmit, or drop the dependency with scontrol update '
            f'JobId={job_id} Dependency= to let it run.',
            found,
        )
    sentences = []
    if blocking:
        names = ', '.join(_name(b) for b in blocking)
        sentences.append(f'Waiting for job {names} to finish.')
    if parsed['singleton']:
        sentences.append(
            'Waiting for earlier jobs with the same name and user to finish '
            '(singleton).')
    if not sentences:
        return _result(
            CATEGORY_DEPENDENCY,
            code,
            f'Waiting on another job ({code}); Slurm did not report which.',
            None,
            found,
        )
    ids = ', '.join(b['job_id'] for b in blocking)
    action = (
        f'Wait for job {ids} to finish; describe it if it is itself stuck.'
        if blocking else 'Wait for the earlier same-name jobs to finish.')
    return _result(CATEGORY_DEPENDENCY, code, ' '.join(sentences), action,
                   found)
