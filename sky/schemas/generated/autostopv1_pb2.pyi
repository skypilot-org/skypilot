from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AutostopWaitFor(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AUTOSTOP_WAIT_FOR_UNSPECIFIED: _ClassVar[AutostopWaitFor]
    AUTOSTOP_WAIT_FOR_JOBS_AND_SSH: _ClassVar[AutostopWaitFor]
    AUTOSTOP_WAIT_FOR_JOBS: _ClassVar[AutostopWaitFor]
    AUTOSTOP_WAIT_FOR_NONE: _ClassVar[AutostopWaitFor]
AUTOSTOP_WAIT_FOR_UNSPECIFIED: AutostopWaitFor
AUTOSTOP_WAIT_FOR_JOBS_AND_SSH: AutostopWaitFor
AUTOSTOP_WAIT_FOR_JOBS: AutostopWaitFor
AUTOSTOP_WAIT_FOR_NONE: AutostopWaitFor

class SetAutostopRequest(_message.Message):
    __slots__ = ("idle_minutes", "backend", "wait_for", "down")
    IDLE_MINUTES_FIELD_NUMBER: _ClassVar[int]
    BACKEND_FIELD_NUMBER: _ClassVar[int]
    WAIT_FOR_FIELD_NUMBER: _ClassVar[int]
    DOWN_FIELD_NUMBER: _ClassVar[int]
    idle_minutes: int
    backend: str
    wait_for: AutostopWaitFor
    down: bool
    def __init__(self, idle_minutes: _Optional[int] = ..., backend: _Optional[str] = ..., wait_for: _Optional[_Union[AutostopWaitFor, str]] = ..., down: bool = ...) -> None: ...

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
