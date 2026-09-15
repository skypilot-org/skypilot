"""The startup breakdown has to survive every hop to the page that draws it.

Three independent allowlists sit between the column and the screen, and each
one names its fields by hand: the job dict the controller builds, the protobuf
message `GetJobTable` fills in, and the dashboard connector's row mapping. A
phase missing from any of them is invisible everywhere downstream, with nothing
failing -- the panel simply renders nothing, exactly as it does for a job that
never ran.

That is not hypothetical. The breakdown shipped with the column written, the
component correct, the page wired up, and two of the three hops dropping it.

So this asserts the chain rather than any one link, driven off the canonical
phase list so a phase added later has to be carried everywhere or go red here.
"""
from google.protobuf import json_format

from sky.jobs import state as managed_job_state
from sky.jobs import utils as managed_job_utils
from sky.metrics import launch_phases
from sky.schemas.api import responses
from sky.schemas.generated import managed_jobsv1_pb2

# What the recorder writes, as the recorder itself defines it. Derived rather
# than restated: a list retyped here would drift from the one that matters.
TIMELINE_COLUMNS = sorted(
    set(launch_phases._JOB_PHASE_COLUMNS.values()) | {'t_time_to_running'})


def test_every_phase_column_crosses_the_controller_boundary():
    """The protobuf must carry each column the breakdown writes.

    `GetJobTable` builds ManagedJobInfo field by field, so a column with no
    field on the message never leaves the jobs controller -- and the API
    server, the dashboard and the operator all see a job with no breakdown.
    """
    fields = {
        f.name for f in managed_jobsv1_pb2.ManagedJobInfo.DESCRIPTOR.fields
    }

    missing = [c for c in TIMELINE_COLUMNS if c not in fields]
    assert not missing, (
        f'{missing} are recorded but have no field on ManagedJobInfo, so they '
        f'stop at the jobs controller')


def test_every_phase_column_is_declared_on_the_response_model():
    """The API serializes through `ManagedJobRecord`, which drops what it does
    not declare.

    This is the link that was still broken after the protobuf was fixed: the
    fields crossed the controller boundary and were then discarded on their way
    out of the API, so the payload the dashboard received had no trace of them.
    """
    declared = set(responses.ManagedJobRecord.model_fields)

    missing = [c for c in TIMELINE_COLUMNS if c not in declared]
    assert not missing, (
        f'{missing} cross the controller boundary but are not declared on '
        f'ManagedJobRecord, so the API drops them on the way out')


def test_the_response_model_keeps_the_values_it_is_given():
    """Declared is not the same as carried: a wrong type would coerce or
    raise, and either way the page would not get the number it draws."""
    recorded = {c: 1.5 + i for i, c in enumerate(TIMELINE_COLUMNS)}

    record = responses.ManagedJobRecord(job_id=1, task_id=0, **recorded)

    for column, value in recorded.items():
        assert getattr(record, column) == value, column


def test_the_columns_the_recorder_writes_are_the_ones_the_readers_expect():
    """One list, four readers.

    `timeline_columns` is what the daemon actually writes to the spot table.
    Every hop below is asserted against it rather than against a list retyped
    per test, so a phase added to the recorder cannot quietly stop at any of
    them.
    """
    # Every phase at once. No single job records all of them -- unattributed
    # is what a job gets *instead* of a breakdown -- but this asks which
    # columns can exist, not which co-occur.
    written = set(
        launch_phases.timeline_columns(
            {phase: 1.0 for phase in launch_phases._JOB_PHASE_COLUMNS}, 15.0))

    assert written == set(TIMELINE_COLUMNS), (
        'the recorder and this suite disagree about which columns exist; '
        'update TIMELINE_COLUMNS and check every hop below')
    # And the spot table actually has them, or nothing upstream matters.
    columns = {c.name for c in managed_job_state.spot_table.columns}
    assert not (written - columns)


def test_the_values_survive_the_round_trip():
    """Set on the message, read back with the values intact.

    The reverse conversion walks the descriptor rather than naming fields, so
    this is really guarding the encoding: a double that comes back as a string,
    or a zero that comes back absent, both break the panel quietly.
    """
    recorded = {c: 1.5 + i for i, c in enumerate(TIMELINE_COLUMNS)}
    proto = managed_jobsv1_pb2.ManagedJobInfo(**recorded)

    out = managed_job_utils._job_proto_to_dict(proto)

    for column, value in recorded.items():
        assert out[column] == value, (column, out.get(column))


def test_a_job_with_no_breakdown_comes_back_as_none_not_zero():
    """Absent has to stay absent across the wire.

    The panel's guard is `!total`, so a 0 substituted for an unset field would
    render an empty bar reading as "started instantly" -- worse than the blank
    the guard is there to produce. This is also what a controller too old to
    send these fields looks like.
    """
    proto = managed_jobsv1_pb2.ManagedJobInfo()

    out = managed_job_utils._job_proto_to_dict(proto)

    for column in TIMELINE_COLUMNS:
        assert out[column] is None, (column, out.get(column))


def test_the_message_and_its_json_form_agree_on_the_field_names():
    """The dashboard reads snake_case names off the JSON payload.

    MessageToDict would otherwise hand it camelCase, and every lookup on the
    page would miss -- the same blank panel, from the other direction.
    """
    proto = managed_jobsv1_pb2.ManagedJobInfo(t_queue_wait=185.0)

    as_json = json_format.MessageToDict(proto, preserving_proto_field_name=True)

    assert 't_queue_wait' in as_json
    assert 'tQueueWait' not in as_json
