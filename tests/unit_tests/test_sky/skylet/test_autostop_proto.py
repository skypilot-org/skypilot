"""Wire-contract tests for the skylet autostop service."""

from sky.schemas.generated import autostopv1_pb2
from sky.skylet import constants


def test_skylet_versions_reload_durable_autodown_runtime():
    assert constants.SKYLET_LIB_VERSION >= 8
    assert int(constants.SKYLET_VERSION) >= 41


def test_set_autostop_request_preserves_fields_and_adds_durable_presence():
    fields = autostopv1_pb2.SetAutostopRequest.DESCRIPTOR.fields_by_name

    assert {name: field.number for name, field in fields.items()} == {
        'idle_minutes': 1,
        'backend': 2,
        'wait_for': 3,
        'down': 4,
        'hook': 5,
        'hook_timeout': 6,
        'hooks': 7,
        'clear_hooks': 8,
        'cluster_hash': 9,
        'generation': 10,
        'execution_strategy': 11,
    }

    old_client_request = autostopv1_pb2.SetAutostopRequest()
    assert not old_client_request.HasField('cluster_hash')
    assert not old_client_request.HasField('generation')
    assert not old_client_request.HasField('execution_strategy')

    explicit_empty_request = autostopv1_pb2.SetAutostopRequest(
        cluster_hash='',
        generation=0,
        execution_strategy=(
            autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_UNSPECIFIED),
    )
    assert explicit_empty_request.HasField('cluster_hash')
    assert explicit_empty_request.HasField('generation')
    assert explicit_empty_request.HasField('execution_strategy')


def test_autostop_responses_add_capability_and_durable_status_fields():
    set_fields = autostopv1_pb2.SetAutostopResponse.DESCRIPTOR.fields_by_name
    assert {name: field.number for name, field in set_fields.items()} == {
        'supports_durable_autodown': 1,
    }

    status_fields = (
        autostopv1_pb2.IsAutostoppingResponse.DESCRIPTOR.fields_by_name)
    assert {name: field.number for name, field in status_fields.items()} == {
        'is_autostopping': 1,
        'supports_durable_autodown': 2,
        'cluster_hash': 3,
        'generation': 4,
        'durable_execution_state': 5,
        'error_summary': 6,
    }


def test_durable_autodown_enums_cover_execution_and_fallback_states():
    assert {
        autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_SERVER_ONLY,
        autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_HEAD_WITH_SERVER_FALLBACK,
        autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_LEGACY_HEAD_CREDENTIALS,
    } == {1, 2, 3}
    assert {
        autostopv1_pb2.DURABLE_AUTODOWN_STATE_ARMED,
        autostopv1_pb2.DURABLE_AUTODOWN_STATE_HEAD_TEARDOWN_STARTED,
        autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED,
    } == {1, 2, 3}


def test_autostop_service_adds_strict_intent_rpc_without_changing_legacy_rpc():
    service = autostopv1_pb2.DESCRIPTOR.services_by_name['AutostopService']

    assert [method.name for method in service.methods] == [
        'SetAutostop',
        'ApplyAutodownIntent',
        'IsAutostopping',
    ]
    apply_intent = service.methods_by_name['ApplyAutodownIntent']
    assert (
        apply_intent.input_type.full_name == 'autostop.v1.SetAutostopRequest')
    assert apply_intent.output_type.full_name == 'autostop.v1.SetAutostopResponse'
