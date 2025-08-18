"""Rpc Utilities for SkyServe"""

from typing import Any, Dict, List, Optional, Tuple

from sky import backends
from sky.backends import backend_utils
from sky.schemas.generated import servev1_pb2
from sky.serve import serve_utils

# ======================= gRPC Converters for Sky Serve =======================


class GetServiceStatusRequestConverter:
    """Converter for GetServiceStatusRequest"""

    @classmethod
    def to_proto(cls, service_names: Optional[List[str]],
                 pool: bool) -> servev1_pb2.GetServiceStatusRequest:
        request = servev1_pb2.GetServiceStatusRequest()
        request.pool = pool
        if service_names is not None:
            request.service_names.names.extend(service_names)
        return request

    @classmethod
    def from_proto(
        cls, proto: servev1_pb2.GetServiceStatusRequest
    ) -> Tuple[Optional[List[str]], bool]:
        pool = proto.pool
        if proto.HasField('service_names'):
            service_names = list(proto.service_names.names)
        else:
            service_names = None
        return service_names, pool


class GetServiceStatusResponseConverter:
    """Converter for GetServiceStatusResponse"""

    @classmethod
    def to_proto(
            cls,
            statuses: List[Dict[str,
                                str]]) -> servev1_pb2.GetServiceStatusResponse:
        response = servev1_pb2.GetServiceStatusResponse()
        for status in statuses:
            added = response.statuses.add()
            added.status.update(status)
        return response

    @classmethod
    def from_proto(
            cls, proto: servev1_pb2.GetServiceStatusResponse
    ) -> List[Dict[str, str]]:
        pickled = [dict(status.status) for status in proto.statuses]
        return pickled


# ========================= gRPC Runner for Sky Serve =========================


class RpcRunner:
    """gRPC Runner for Sky Serve
    The RPC runner does not check for errors, and assumes that backend handle
    has grpc enabled.
    """

    @classmethod
    def get_service_status(cls, handle: backends.CloudVmRayResourceHandle,
                           service_names: Optional[List[str]],
                           pool: bool) -> List[Dict[str, Any]]:
        assert handle.is_grpc_enabled
        request = GetServiceStatusRequestConverter.to_proto(service_names, pool)
        response = backend_utils.invoke_skylet_with_retries(
            handle, lambda: backends.SkyletClient(handle.get_grpc_channel()).
            get_service_status(request))
        pickled = GetServiceStatusResponseConverter.from_proto(response)
        return serve_utils.unpickle_service_status(pickled)

    @classmethod
    def add_version(cls, handle: backends.CloudVmRayResourceHandle,
                    service_name: str) -> int:
        assert handle.is_grpc_enabled
        request = servev1_pb2.AddVersionRequest(service_name=service_name)
        response = backend_utils.invoke_skylet_with_retries(
            handle, lambda: backends.SkyletClient(handle.get_grpc_channel()).
            add_serve_version(request))
        return response.version
