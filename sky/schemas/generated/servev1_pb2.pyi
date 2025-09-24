from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ServiceNames(_message.Message):
    __slots__ = ("names",)
    NAMES_FIELD_NUMBER: _ClassVar[int]
    names: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, names: _Optional[_Iterable[str]] = ...) -> None: ...

class ServiceStatus(_message.Message):
    __slots__ = ("status",)
    class StatusEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: _containers.ScalarMap[str, str]
    def __init__(self, status: _Optional[_Mapping[str, str]] = ...) -> None: ...

class GetServiceStatusRequest(_message.Message):
    __slots__ = ("service_names", "pool")
    SERVICE_NAMES_FIELD_NUMBER: _ClassVar[int]
    POOL_FIELD_NUMBER: _ClassVar[int]
    service_names: ServiceNames
    pool: bool
    def __init__(self, service_names: _Optional[_Union[ServiceNames, _Mapping]] = ..., pool: bool = ...) -> None: ...

class GetServiceStatusResponse(_message.Message):
    __slots__ = ("statuses",)
    STATUSES_FIELD_NUMBER: _ClassVar[int]
    statuses: _containers.RepeatedCompositeFieldContainer[ServiceStatus]
    def __init__(self, statuses: _Optional[_Iterable[_Union[ServiceStatus, _Mapping]]] = ...) -> None: ...

class AddVersionRequest(_message.Message):
    __slots__ = ("service_name",)
    SERVICE_NAME_FIELD_NUMBER: _ClassVar[int]
    service_name: str
    def __init__(self, service_name: _Optional[str] = ...) -> None: ...

class AddVersionResponse(_message.Message):
    __slots__ = ("version",)
    VERSION_FIELD_NUMBER: _ClassVar[int]
    version: int
    def __init__(self, version: _Optional[int] = ...) -> None: ...

class TerminateServicesRequest(_message.Message):
    __slots__ = ("service_names", "purge", "pool")
    SERVICE_NAMES_FIELD_NUMBER: _ClassVar[int]
    PURGE_FIELD_NUMBER: _ClassVar[int]
    POOL_FIELD_NUMBER: _ClassVar[int]
    service_names: ServiceNames
    purge: bool
    pool: bool
    def __init__(self, service_names: _Optional[_Union[ServiceNames, _Mapping]] = ..., purge: bool = ..., pool: bool = ...) -> None: ...

class TerminateServicesResponse(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: str
    def __init__(self, message: _Optional[str] = ...) -> None: ...

class TerminateReplicaRequest(_message.Message):
    __slots__ = ("service_name", "replica_id", "purge")
    SERVICE_NAME_FIELD_NUMBER: _ClassVar[int]
    REPLICA_ID_FIELD_NUMBER: _ClassVar[int]
    PURGE_FIELD_NUMBER: _ClassVar[int]
    service_name: str
    replica_id: int
    purge: bool
    def __init__(self, service_name: _Optional[str] = ..., replica_id: _Optional[int] = ..., purge: bool = ...) -> None: ...

class TerminateReplicaResponse(_message.Message):
    __slots__ = ("message",)
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    message: str
    def __init__(self, message: _Optional[str] = ...) -> None: ...

class WaitServiceRegistrationRequest(_message.Message):
    __slots__ = ("service_name", "job_id", "pool")
    SERVICE_NAME_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    POOL_FIELD_NUMBER: _ClassVar[int]
    service_name: str
    job_id: int
    pool: bool
    def __init__(self, service_name: _Optional[str] = ..., job_id: _Optional[int] = ..., pool: bool = ...) -> None: ...

class WaitServiceRegistrationResponse(_message.Message):
    __slots__ = ("lb_port",)
    LB_PORT_FIELD_NUMBER: _ClassVar[int]
    lb_port: int
    def __init__(self, lb_port: _Optional[int] = ...) -> None: ...

class UpdateServiceRequest(_message.Message):
    __slots__ = ("service_name", "version", "mode", "pool")
    SERVICE_NAME_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    POOL_FIELD_NUMBER: _ClassVar[int]
    service_name: str
    version: int
    mode: str
    pool: bool
    def __init__(self, service_name: _Optional[str] = ..., version: _Optional[int] = ..., mode: _Optional[str] = ..., pool: bool = ...) -> None: ...

class UpdateServiceResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...
