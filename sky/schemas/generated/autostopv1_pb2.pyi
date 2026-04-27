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

class Event(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EVENT_UNSPECIFIED: _ClassVar[Event]
    AUTOSTOP: _ClassVar[Event]
    PREEMPTION: _ClassVar[Event]
    DOWN: _ClassVar[Event]
AUTOSTOP_WAIT_FOR_UNSPECIFIED: AutostopWaitFor
AUTOSTOP_WAIT_FOR_JOBS_AND_SSH: AutostopWaitFor
AUTOSTOP_WAIT_FOR_JOBS: AutostopWaitFor
AUTOSTOP_WAIT_FOR_NONE: AutostopWaitFor
EVENT_UNSPECIFIED: Event
AUTOSTOP: Event
PREEMPTION: Event
DOWN: Event

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
    __slots__ = ("idle_minutes", "backend", "wait_for", "down", "hook", "hook_timeout", "hooks", "clear_hooks")
    IDLE_MINUTES_FIELD_NUMBER: _ClassVar[int]
    BACKEND_FIELD_NUMBER: _ClassVar[int]
    WAIT_FOR_FIELD_NUMBER: _ClassVar[int]
    DOWN_FIELD_NUMBER: _ClassVar[int]
    HOOK_FIELD_NUMBER: _ClassVar[int]
    HOOK_TIMEOUT_FIELD_NUMBER: _ClassVar[int]
    HOOKS_FIELD_NUMBER: _ClassVar[int]
    CLEAR_HOOKS_FIELD_NUMBER: _ClassVar[int]
    idle_minutes: int
    backend: str
    wait_for: AutostopWaitFor
    down: bool
    hook: str
    hook_timeout: int
    hooks: _containers.RepeatedCompositeFieldContainer[Hook]
    clear_hooks: bool
    def __init__(self, idle_minutes: _Optional[int] = ..., backend: _Optional[str] = ..., wait_for: _Optional[_Union[AutostopWaitFor, str]] = ..., down: bool = ..., hook: _Optional[str] = ..., hook_timeout: _Optional[int] = ..., hooks: _Optional[_Iterable[_Union[Hook, _Mapping]]] = ..., clear_hooks: bool = ...) -> None: ...

class SetAutostopResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class IsAutostoppingRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class IsAutostoppingResponse(_message.Message):
    __slots__ = ("is_autostopping",)
    IS_AUTOSTOPPING_FIELD_NUMBER: _ClassVar[int]
    is_autostopping: bool
    def __init__(self, is_autostopping: bool = ...) -> None: ...
