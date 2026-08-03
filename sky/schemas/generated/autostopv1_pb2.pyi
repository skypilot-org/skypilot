from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AutostopWaitFor(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AUTOSTOP_WAIT_FOR_UNSPECIFIED: _ClassVar[AutostopWaitFor]
    AUTOSTOP_WAIT_FOR_JOBS_AND_SSH: _ClassVar[AutostopWaitFor]
    AUTOSTOP_WAIT_FOR_JOBS: _ClassVar[AutostopWaitFor]
    AUTOSTOP_WAIT_FOR_NONE: _ClassVar[AutostopWaitFor]

class AutodownExecutionStrategy(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AUTODOWN_EXECUTION_STRATEGY_UNSPECIFIED: _ClassVar[AutodownExecutionStrategy]
    AUTODOWN_EXECUTION_STRATEGY_SERVER_ONLY: _ClassVar[AutodownExecutionStrategy]
    AUTODOWN_EXECUTION_STRATEGY_HEAD_WITH_SERVER_FALLBACK: _ClassVar[AutodownExecutionStrategy]
    AUTODOWN_EXECUTION_STRATEGY_LEGACY_HEAD_CREDENTIALS: _ClassVar[AutodownExecutionStrategy]

class DurableAutodownState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DURABLE_AUTODOWN_STATE_UNSPECIFIED: _ClassVar[DurableAutodownState]
    DURABLE_AUTODOWN_STATE_ARMED: _ClassVar[DurableAutodownState]
    DURABLE_AUTODOWN_STATE_HEAD_TEARDOWN_STARTED: _ClassVar[DurableAutodownState]
    DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED: _ClassVar[DurableAutodownState]

class Event(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EVENT_UNSPECIFIED: _ClassVar[Event]
    EVENT_STOP: _ClassVar[Event]
    EVENT_PREEMPTION: _ClassVar[Event]
    EVENT_DOWN: _ClassVar[Event]
AUTOSTOP_WAIT_FOR_UNSPECIFIED: AutostopWaitFor
AUTOSTOP_WAIT_FOR_JOBS_AND_SSH: AutostopWaitFor
AUTOSTOP_WAIT_FOR_JOBS: AutostopWaitFor
AUTOSTOP_WAIT_FOR_NONE: AutostopWaitFor
AUTODOWN_EXECUTION_STRATEGY_UNSPECIFIED: AutodownExecutionStrategy
AUTODOWN_EXECUTION_STRATEGY_SERVER_ONLY: AutodownExecutionStrategy
AUTODOWN_EXECUTION_STRATEGY_HEAD_WITH_SERVER_FALLBACK: AutodownExecutionStrategy
AUTODOWN_EXECUTION_STRATEGY_LEGACY_HEAD_CREDENTIALS: AutodownExecutionStrategy
DURABLE_AUTODOWN_STATE_UNSPECIFIED: DurableAutodownState
DURABLE_AUTODOWN_STATE_ARMED: DurableAutodownState
DURABLE_AUTODOWN_STATE_HEAD_TEARDOWN_STARTED: DurableAutodownState
DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED: DurableAutodownState
EVENT_UNSPECIFIED: Event
EVENT_STOP: Event
EVENT_PREEMPTION: Event
EVENT_DOWN: Event

class Hook(_message.Message):
    __slots__ = ("run", "events", "timeout")
    RUN_FIELD_NUMBER: _ClassVar[int]
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_FIELD_NUMBER: _ClassVar[int]
    run: str
    events: _containers.RepeatedScalarFieldContainer[Event]
    timeout: int
    def __init__(self, run: _Optional[str] = ..., events: _Optional[_Iterable[_Union[Event, str]]] = ..., timeout: _Optional[int] = ...) -> None: ...

class SetAutostopRequest(_message.Message):
    __slots__ = ("idle_minutes", "backend", "wait_for", "down", "hook", "hook_timeout", "hooks", "clear_hooks", "cluster_hash", "generation", "execution_strategy")
    IDLE_MINUTES_FIELD_NUMBER: _ClassVar[int]
    BACKEND_FIELD_NUMBER: _ClassVar[int]
    WAIT_FOR_FIELD_NUMBER: _ClassVar[int]
    DOWN_FIELD_NUMBER: _ClassVar[int]
    HOOK_FIELD_NUMBER: _ClassVar[int]
    HOOK_TIMEOUT_FIELD_NUMBER: _ClassVar[int]
    HOOKS_FIELD_NUMBER: _ClassVar[int]
    CLEAR_HOOKS_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_HASH_FIELD_NUMBER: _ClassVar[int]
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_STRATEGY_FIELD_NUMBER: _ClassVar[int]
    idle_minutes: int
    backend: str
    wait_for: AutostopWaitFor
    down: bool
    hook: str
    hook_timeout: int
    hooks: _containers.RepeatedCompositeFieldContainer[Hook]
    clear_hooks: bool
    cluster_hash: str
    generation: int
    execution_strategy: AutodownExecutionStrategy
    def __init__(self, idle_minutes: _Optional[int] = ..., backend: _Optional[str] = ..., wait_for: _Optional[_Union[AutostopWaitFor, str]] = ..., down: bool = ..., hook: _Optional[str] = ..., hook_timeout: _Optional[int] = ..., hooks: _Optional[_Iterable[_Union[Hook, _Mapping]]] = ..., clear_hooks: bool = ..., cluster_hash: _Optional[str] = ..., generation: _Optional[int] = ..., execution_strategy: _Optional[_Union[AutodownExecutionStrategy, str]] = ...) -> None: ...

class SetAutostopResponse(_message.Message):
    __slots__ = ("supports_durable_autodown",)
    SUPPORTS_DURABLE_AUTODOWN_FIELD_NUMBER: _ClassVar[int]
    supports_durable_autodown: bool
    def __init__(self, supports_durable_autodown: bool = ...) -> None: ...

class IsAutostoppingRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class IsAutostoppingResponse(_message.Message):
    __slots__ = ("is_autostopping", "supports_durable_autodown", "cluster_hash", "generation", "durable_execution_state", "error_summary")
    IS_AUTOSTOPPING_FIELD_NUMBER: _ClassVar[int]
    SUPPORTS_DURABLE_AUTODOWN_FIELD_NUMBER: _ClassVar[int]
    CLUSTER_HASH_FIELD_NUMBER: _ClassVar[int]
    GENERATION_FIELD_NUMBER: _ClassVar[int]
    DURABLE_EXECUTION_STATE_FIELD_NUMBER: _ClassVar[int]
    ERROR_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    is_autostopping: bool
    supports_durable_autodown: bool
    cluster_hash: str
    generation: int
    durable_execution_state: DurableAutodownState
    error_summary: str
    def __init__(self, is_autostopping: bool = ..., supports_durable_autodown: bool = ..., cluster_hash: _Optional[str] = ..., generation: _Optional[int] = ..., durable_execution_state: _Optional[_Union[DurableAutodownState, str]] = ..., error_summary: _Optional[str] = ...) -> None: ...
