"""Tests for the Slurm pending taxonomy (provision/slurm/pending.py)."""

import pytest

from sky.provision.slurm import pending as sp

QOSES = {
    'post_training_limit': {
        'name': 'post_training_limit',
        'nominal': {
            'gres/gpu': 736
        },
        'max_tres_per_user': {
            'gres/gpu': 64
        },
    },
    'normal': {
        'name': 'normal',
        'nominal': {},
        'max_tres_per_user': {}
    },
}
PARTITIONS = {
    'post-training': {
        'name': 'post-training',
        'qos': 'post_training_limit'
    },
    'general': {
        'name': 'general',
        'qos': 'normal'
    },
}


def _job(reason, **kw):
    row = {
        'job_id': '5123',
        'state': 'PENDING',
        'reason': reason,
        'partition': 'h200'
    }
    row.update(kw)
    return row


def _cat(reason):
    return sp.pending_category(sp.pending_reason_code(_job(reason)))


# --- category mapping ---------------------------------------------------------


@pytest.mark.parametrize(
    'reason, category',
    [
        ('QOSGrpGRES', 'quota'),
        ('(QOSGrpGRES)', 'quota'),
        ('QOSMaxGRESPerUser', 'quota'),
        ('QOSMaxCpuPerJobLimit', 'quota'),
        ('AssocGrpGRES', 'quota'),
        ('AssocMaxJobsLimit', 'quota'),
        ('QOSUsageThreshold', 'quota'),
        ('Resources', 'resources'),
        ('Priority', 'resources'),
        ('ReqNodeNotAvail, UnavailableNodes:gpu-3', 'resources'),
        (
            'Nodes required for job are DOWN, DRAINED or reserved for jobs in '
            'higher priority partitions',
            'resources',
        ),
        ('PartitionDown', 'resources'),
        ('Reservation', 'resources'),
        ('JobHeldUser', 'held'),
        ('JobHeldAdmin', 'held'),
        ('launch failed requeued held', 'held'),
        ('BeginTime', 'held'),
        ('Dependency', 'dependency'),
        ('DependencyNeverSatisfied', 'dependency'),
        ('BadConstraints', 'other'),
        ('SomethingNew', 'other'),
    ],
)
def test_pending_category(reason, category):
    assert _cat(reason) == category


def test_not_pending_or_no_reason_yields_none():
    assert sp.classify_pending({
        'state': 'RUNNING',
        'reason': 'gpu-[1-4]'
    }) is None
    assert sp.classify_pending(_job('(None)')) is None
    assert sp.classify_pending(_job('')) is None


def test_result_shape_and_whitespace_collapsed():
    out = sp.classify_pending(_job('Resources  '))
    assert set(out) == {'category', 'code', 'summary', 'action', 'evidence'}
    assert out['code'] == 'Resources'
    assert '  ' not in out['summary']


# --- quota ----------------------------------------------------------------------


def test_quota_with_cap_and_usage_names_the_full_quota():
    job = _job('QOSGrpGRES',
               partition='post-training',
               qos='normal',
               gpu_type='H200')
    ev = sp.PendingEvidence(
        qoses=QOSES,
        partitions=PARTITIONS,
        qos_usage={
            'post_training_limit': {
                'gres/gpu': 736,
                'running': 9,
                'pending': 3
            }
        },
        effective_qos=['post_training_limit'],
        blocking_resource='H200',
    )
    out = sp.classify_pending(job, ev)
    assert out['category'] == 'quota'
    assert out['summary'] == (
        'H200 quota for QoS post_training_limit is full (736/736).')
    assert 'post_training_limit' in out['action']
    assert out['evidence'] == {
        'qos': ['post_training_limit'],
        'cap': 736,
        'cap_tres': 'gres/gpu',
        'used': 736,
        'resource': 'H200',
    }


def test_quota_with_cap_but_no_usage_still_names_the_cap():
    job = _job('QOSGrpGRES', partition='post-training', qos='normal')
    out = sp.classify_pending(
        job,
        sp.PendingEvidence(
            qoses=QOSES,
            partitions=PARTITIONS,
            effective_qos=['post_training_limit'],
        ),
    )
    assert out[
        'summary'] == 'GPUs quota for QoS post_training_limit is full (cap 736).'
    assert out['action']


def test_quota_typed_cap_does_not_borrow_untyped_gpu_usage():
    """A typed cap and the QoS-wide untyped total are different numbers;
    pairing them printed ratios like 32/16."""
    qoses = {
        'typed_limit': {
            'name': 'typed_limit',
            'nominal': {
                'gres/gpu:a100': 16
            }
        },
        'normal': {
            'name': 'normal',
            'nominal': {},
            'max_tres_per_user': {}
        },
    }
    partitions = {'a100': {'name': 'a100', 'qos': 'typed_limit'}}
    job = _job('QOSGrpGRES', partition='a100', qos='normal')
    ev = sp.PendingEvidence(
        qoses=qoses,
        partitions=partitions,
        qos_usage={'typed_limit': {
            'gres/gpu': 32,
            'running': 4,
            'pending': 1
        }},
        effective_qos=['typed_limit'],
    )
    out = sp.classify_pending(job, ev)
    assert out['summary'].endswith('is full (cap 16).')
    assert out['evidence']['used'] is None
    assert out['evidence']['cap_tres'] == 'gres/gpu:a100'


def test_quota_non_gpu_cap_does_not_borrow_gpu_usage():
    qoses = {
        'fpga_limit': {
            'name': 'fpga_limit',
            'nominal': {
                'gres/fpga': 4
            }
        },
        'normal': {
            'name': 'normal',
            'nominal': {},
            'max_tres_per_user': {}
        },
    }
    partitions = {'fpga': {'name': 'fpga', 'qos': 'fpga_limit'}}
    job = _job('QOSGrpGRES', partition='fpga', qos='normal')
    ev = sp.PendingEvidence(
        qoses=qoses,
        partitions=partitions,
        qos_usage={'fpga_limit': {
            'gres/gpu': 8
        }},
        effective_qos=['fpga_limit'],
    )
    out = sp.classify_pending(job, ev)
    assert out['summary'].endswith('is full (cap 4).')
    assert out['evidence']['used'] is None


def test_quota_without_accounting_says_the_cap_cannot_be_read():
    job = _job('QOSGrpGRES', qos='post_training_limit')
    ev = sp.PendingEvidence(
        qoses={},
        accounting_error='sacctmgr: command not found',
        effective_qos=['post_training_limit'],
    )
    out = sp.classify_pending(job, ev)
    assert out['category'] == 'quota'
    assert 'could not be read' in out['summary']
    assert 'sacctmgr: command not found' in out['summary']
    assert out['action'] is None
    assert out['evidence']['qos'] == ['post_training_limit']


def test_quota_with_no_evidence_at_all_has_no_action():
    out = sp.classify_pending(_job('QOSGrpGRES'))
    assert out['category'] == 'quota'
    assert out['action'] is None
    assert out['evidence']['qos'] is None


def test_quota_multi_candidate_qos_does_not_pick_one():
    job = _job('QOSGrpGRES', partition='post-training,general', qos='normal')
    out = sp.classify_pending(
        job,
        sp.PendingEvidence(
            qoses=QOSES,
            partitions=PARTITIONS,
            # A job submitted to a partition list counts against several QoS;
            # the caller resolved both and neither is the one Slurm charged.
            effective_qos=['post_training_limit', 'normal'],
        ),
    )
    assert 'post_training_limit, normal' in out['summary']
    assert out['action'] is None


def test_quota_per_user_limit_reports_the_per_user_cap():
    job = _job('QOSMaxGRESPerUser', qos='post_training_limit')
    out = sp.classify_pending(
        job,
        sp.PendingEvidence(qoses=QOSES,
                           partitions={},
                           effective_qos=['post_training_limit']),
    )
    assert 'Per-user limit QOSMaxGRESPerUser' in out['summary']
    assert 'gres/gpu=64' in out['summary']
    assert 'your own jobs' in out['action']


def test_quota_association_limit():
    job = _job('AssocGrpCPURunMinutesLimit', qos='post_training_limit')
    out = sp.classify_pending(
        job,
        sp.PendingEvidence(qoses=QOSES,
                           partitions={},
                           effective_qos=['post_training_limit']),
    )
    assert out['category'] == 'quota'
    assert 'association' in out['summary']
    assert 'sacctmgr show assoc' in out['action']


# --- resources ------------------------------------------------------------------


def test_resources_with_node_counts_and_queue_position():
    ev = sp.PendingEvidence(
        partition_nodes={
            'total': 64,
            'idle': 0,
            'allocated': 60,
            'drained': 4,
            'down': 0,
        },
        pending_ahead=3,
    )
    out = sp.classify_pending(_job('Resources'), ev)
    assert out['category'] == 'resources'
    assert out['summary'] == (
        'No idle node in partition h200 (64 nodes: 60 allocated, 4 drained). '
        '3 jobs are pending ahead of this one.')
    assert out['action']
    assert out['evidence']['pending_ahead'] == 3


def test_resources_idle_nodes_that_do_not_fit():
    ev = sp.PendingEvidence(partition_nodes={
        'total': 8,
        'idle': 2,
        'allocated': 6,
        'drained': 0,
        'down': 0
    })
    out = sp.classify_pending(_job('Resources'), ev)
    assert out['summary'].startswith(
        'The 2 idle nodes in partition h200 do not satisfy')


def test_resources_does_not_claim_idle_nodes_when_they_are_unavailable():
    """The wrong sentence this guards against: 'the 3 idle nodes do not
    satisfy this job's request' about nodes under maintenance."""
    ev = sp.PendingEvidence(
        partition_nodes={
            'total': 5,
            'idle': 0,
            'allocated': 2,
            'drained': 0,
            'down': 0,
            'unavailable': 3,
            'powered_down': 0,
        })
    out = sp.classify_pending(_job('Resources', partition='h200'), ev)
    assert out['summary'] == (
        'No idle node in partition h200 (5 nodes: 2 allocated, 3 unavailable).')
    assert 'idle node' not in out['summary'].replace('No idle node', '')


def test_resources_names_powered_down_nodes():
    """A partition that is entirely powered down used to render as
    '0 nodes: 0 allocated', which reads as broken rather than asleep."""
    ev = sp.PendingEvidence(
        partition_nodes={
            'total': 0,
            'idle': 0,
            'allocated': 0,
            'drained': 0,
            'down': 0,
            'unavailable': 0,
            'powered_down': 4,
        })
    out = sp.classify_pending(_job('Resources', partition='h200'), ev)
    assert '4 powered down' in out['summary']


def test_priority_with_nothing_queued_ahead_does_not_contradict_itself():
    """Seen on a real cluster: "Higher-priority jobs are ahead of this one ...
    0 jobs are pending ahead of this one." Both halves were true -- what was
    ahead was running, not queued -- and together they read as nonsense."""
    ev = sp.PendingEvidence(
        partition_nodes={
            'total': 3,
            'idle': 0,
            'allocated': 3,
            'drained': 0,
            'down': 0,
            'unavailable': 0,
            'powered_down': 0,
        },
        pending_ahead=0,
    )
    out = sp.classify_pending(_job('Priority', partition='dev'), ev)
    assert '0 jobs are pending' not in out['summary'], out['summary']
    assert out['summary'].endswith('No other pending job is ahead of it.')


def test_resources_without_evidence_has_no_action():
    out = sp.classify_pending(_job('Resources'))
    assert out[
        'summary'] == 'Waiting for free resources in partition h200 (Resources).'
    assert out['action'] is None


def test_priority_with_counts():
    ev = sp.PendingEvidence(
        partition_nodes={
            'total': 4,
            'idle': 0,
            'allocated': 4,
            'drained': 0,
            'down': 0,
        },
        pending_ahead=1,
    )
    out = sp.classify_pending(_job('Priority'), ev)
    assert out['summary'].startswith('Higher-priority jobs are ahead')
    assert '1 job is pending ahead' in out['summary']


def test_req_node_not_avail_names_the_nodes():
    out = sp.classify_pending(
        _job('ReqNodeNotAvail, UnavailableNodes:gpu-[3-4]'))
    assert out['category'] == 'resources'
    assert 'gpu-[3-4]' in out['summary']
    assert out['evidence']['unavailable_nodes'] == 'gpu-[3-4]'
    assert '--nodelist' in out['action']


def test_partition_down():
    out = sp.classify_pending(_job('PartitionDown'))
    assert out['summary'] == 'Partition h200 is down and not scheduling jobs.'
    assert 'administrator' in out['action']


# --- held ---------------------------------------------------------------------------


def test_held_after_failed_launch_with_restarts():
    out = sp.classify_pending(_job('launch failed requeued held'),
                              sp.PendingEvidence(restarts=2))
    assert out['category'] == 'held'
    assert out['summary'] == 'Held after 2 requeues: the job launch failed.'
    assert out['action'] == (
        'Check the node and prolog logs for the failure, then release it with '
        'scontrol release 5123.')
    assert out['evidence'] == {'restarts': 2}


def test_held_after_failed_launch_without_restarts():
    out = sp.classify_pending(_job('launch failed requeued held'))
    assert out[
        'summary'] == 'Held: the job launch failed and the job was requeued.'
    assert 'scontrol release 5123' in out['action']


def test_held_by_user_and_admin():
    user = sp.classify_pending(_job('JobHeldUser'),
                               sp.PendingEvidence(restarts=1))
    assert user['summary'].startswith('Held by the user')
    assert 'requeued 1 time' in user['summary']
    assert 'scontrol release 5123' in user['action']
    admin = sp.classify_pending(_job('JobHeldAdmin'))
    assert admin['summary'] == 'Held by an administrator.'
    assert 'administrator' in admin['action']


def test_begin_time_with_and_without_the_time():
    out = sp.classify_pending(_job('BeginTime'),
                              sp.PendingEvidence(begin_time='1700000000'))
    assert '2023-11-14 22:13:20 UTC' in out['summary']
    assert 'StartTime=now' in out['action']
    bare = sp.classify_pending(_job('BeginTime'))
    assert (bare['summary'] ==
            'Not eligible yet: the requested begin time is in the future.')


# --- dependency -----------------------------------------------------------------


def test_begin_time_slurm_has_not_computed_says_nothing_about_when():
    """Slurm answers `Unknown` for a time it has not worked out yet, and that
    used to be pasted straight into the sentence: "It becomes eligible at
    Unknown." Saying nothing about when is the honest version."""
    job = _job('BeginTime')
    out = sp.classify_pending(job, sp.PendingEvidence(begin_time='Unknown'))
    assert out['summary'] == (
        'Not eligible yet: the requested begin time is in the future.')
    assert out['evidence']['begin_time'] is None
    # The action still tells the caller how to start it now.
    assert 'StartTime=now' in out['action']


def test_a_begin_time_that_is_not_epoch_is_shown_as_it_came():
    """The reads ask for epoch seconds; a Slurm build old enough to ignore
    that answers in ISO form, in the cluster's local timezone with nothing
    saying which. Showing it verbatim beats both dropping it and converting
    it against this host's timezone."""
    out = sp.classify_pending(
        _job('BeginTime'), sp.PendingEvidence(begin_time='2026-09-08T04:05:47'))
    assert '2026-09-08 04:05:47' in out['summary']
    # No timezone is claimed for it, unlike the epoch form.
    assert 'UTC' not in out['summary']


def test_dependency_waiting_with_known_state():
    job = _job('Dependency', dependency='afterok:5122(unfulfilled)')
    ev = sp.PendingEvidence(dependency_states={'5122': 'RUNNING'})
    out = sp.classify_pending(job, ev)
    assert out['category'] == 'dependency'
    assert out['summary'] == 'Waiting for job 5122 (RUNNING) to finish.'
    assert out['action'] == (
        'Wait for job 5122 to finish; describe it if it is itself stuck.')
    assert out['evidence']['blocking_jobs'] == [{
        'job_id': '5122',
        'state': 'RUNNING'
    }]
    assert out['evidence']['unsatisfiable'] is False


def test_dependency_on_a_job_that_left_the_queue():
    job = _job('Dependency', dependency='afterany:5122(unfulfilled)')
    out = sp.classify_pending(job, sp.PendingEvidence(dependency_states={}))
    assert out[
        'summary'] == 'Waiting for job 5122 (no longer in the queue) to finish.'


def test_dependency_without_sweep_names_the_job_only():
    job = _job('Dependency', dependency='afterok:5122')
    out = sp.classify_pending(job)
    assert out['summary'] == 'Waiting for job 5122 to finish.'


def test_dependency_never_satisfied():
    job = _job('DependencyNeverSatisfied', dependency='afterok:5120(failed)')
    out = sp.classify_pending(job)
    assert out['summary'].startswith('Depends on job 5120 which failed')
    assert 'JobId=5123 Dependency=' in out['action']
    assert out['evidence']['unsatisfiable'] is True


def test_dependency_any_of_survives_one_failed_alternative():
    """'?' is OR: the job runs as soon as 5121 succeeds, so the failed
    alternative must not read as a dead end."""
    job = _job('Dependency',
               dependency='afterok:5120(failed)?afterok:5121(unfulfilled)')
    out = sp.classify_pending(job)
    assert out['evidence']['unsatisfiable'] is False
    assert out['summary'].startswith('Waiting for job')
    assert 'never run' not in out['summary']
    assert out['evidence']['failed_dependencies'] == ['5120']


def test_dependency_any_of_with_every_alternative_failed_is_fatal():
    job = _job('Dependency',
               dependency='afterok:5120(failed)?afterok:5121(failed)')
    out = sp.classify_pending(job)
    assert out['evidence']['unsatisfiable'] is True
    assert out['summary'].startswith('Depends on job 5120, 5121 which failed')


def test_dependency_all_of_names_only_the_failed_entry():
    job = _job('Dependency',
               dependency='afterok:5120(failed),afterok:5121(unfulfilled)')
    out = sp.classify_pending(job)
    assert out['evidence']['unsatisfiable'] is True
    assert out['summary'].startswith('Depends on job 5120 which failed')


def test_dependency_never_satisfied_reason_wins_over_the_expression():
    """Slurm's own verdict stands even when no entry is annotated failed."""
    job = _job('DependencyNeverSatisfied',
               dependency='afterok:5120?afterok:5121')
    out = sp.classify_pending(job)
    assert out['evidence']['unsatisfiable'] is True
    assert out['summary'].startswith('Depends on job 5120, 5121 which failed')


def test_a_failed_dependency_is_named_as_failed_not_as_missing():
    """It has left the queue precisely because it failed, so the sweep has no
    state for it -- and "no longer in the queue" is the less useful of the two
    things known about it."""
    job = _job('Dependency',
               dependency='afterok:5120(failed)?afterok:5121(unfulfilled)')
    out = sp.classify_pending(
        job, sp.PendingEvidence(dependency_states={'5121': 'PENDING'}))
    assert '5120 (failed)' in out['summary'], out['summary']
    assert 'no longer in the queue' not in out['summary']


def test_dependency_singleton():
    job = _job('Dependency', dependency='singleton(unfulfilled)')
    out = sp.classify_pending(job)
    assert 'singleton' in out['summary']
    assert out['evidence']['singleton'] is True


def test_dependency_without_expression_has_no_action():
    out = sp.classify_pending(_job('Dependency'))
    assert 'Slurm did not report which' in out['summary']
    assert out['action'] is None


# --- other ------------------------------------------------------------------------


def test_other_returns_the_raw_code_only():
    out = sp.classify_pending(_job('BadConstraints'))
    assert out == {
        'category': 'other',
        'code': 'BadConstraints',
        'summary': 'BadConstraints',
        'action': None,
        'evidence': {},
    }


# --- moved helpers still behave ------------------------------------------------


def test_the_pure_parses_survived_the_move():
    parsed = sp.parse_dependency(
        'afterok:5122(unfulfilled),afterany:99_4+30(failed)')
    assert parsed == {
        'job_ids': ['5122', '99_4'],
        'failed_job_ids': ['99_4'],
        'singleton': False,
        'unsatisfiable': True,
    }
    assert sp.pending_reason_code({
        'state': 'PENDING',
        'reason': '(QOSGrpGRES)'
    }) == ('QOSGrpGRES')
    # resolve_effective_qos and the GRES label helpers are NOT here: which
    # QoS a partition attaches, and which resource a cap names, are rules
    # about the accounting database, so the caller that reads it owns them and
    # passes the answers in as evidence.
    for gone in ('resolve_effective_qos', 'blocking_gres', 'gres_cap_labels'):
        assert not hasattr(sp, gone), gone


# --- pending_ahead ordering -----------------------------------------------


def _queued(job_id, priority, partition='h200', state='PENDING'):
    return {
        'job_id': job_id,
        'partition': partition,
        'priority': str(priority),
        'state': state,
    }


def test_a_higher_priority_job_is_ahead():
    mine = {'job_id': '100', 'partition': 'h200', 'priority': '500'}
    sweep = [_queued('99', 900), _queued('101', 10)]
    assert sp.pending_ahead(sweep, mine) == 1


def test_an_older_job_at_the_same_priority_is_ahead():
    """Measured on a cluster with no multifactor priority: every job reports
    priority 1, so counting only strictly-higher priorities answered "nothing
    is ahead of you" while an older job was plainly next. Slurm breaks the tie
    by submission, which the id orders."""
    mine = {'job_id': '17269', 'partition': 'dev', 'priority': '1'}
    sweep = [
        _queued('17260', 1, partition='dev'),  # older, so ahead
        _queued('17999', 1, partition='dev'),  # newer, so behind
    ]
    assert sp.pending_ahead(sweep, mine) == 1


def test_an_array_element_orders_by_its_base_id():
    mine = {'job_id': '17300_4', 'partition': 'dev', 'priority': '1'}
    sweep = [_queued('17221_9', 1, partition='dev')]
    assert sp.pending_ahead(sweep, mine) == 1


def test_a_job_in_another_partition_is_not_ahead():
    mine = {'job_id': '17269', 'partition': 'dev', 'priority': '1'}
    assert sp.pending_ahead([_queued('17260', 1, partition='cpu')], mine) == 0


def test_a_running_job_is_not_counted_as_ahead():
    mine = {'job_id': '17269', 'partition': 'dev', 'priority': '1'}
    sweep = [_queued('17260', 1, partition='dev', state='RUNNING')]
    assert sp.pending_ahead(sweep, mine) == 0


def test_an_unparseable_id_at_the_same_priority_is_not_guessed():
    mine = {'job_id': '17269', 'partition': 'dev', 'priority': '1'}
    assert sp.pending_ahead([_queued('weird', 1, partition='dev')], mine) == 0


def test_two_elements_of_one_array_do_not_count_each_other():
    """A known limit, locked in deliberately. Both elements collapse to the
    same base id, so neither counts the other as ahead -- an element with a
    late index can therefore under-report. Ordering array elements against
    each other needs the index, which squeue does not give here; before the
    tie-break existed no tie counted at all, so this is unchanged behaviour
    rather than a regression."""
    mine = {'job_id': '17221_9', 'partition': 'dev', 'priority': '1'}
    sweep = [_queued('17221_2', 1, partition='dev')]
    assert sp.pending_ahead(sweep, mine) == 0
