"""gRPC service implementations for skylet."""

import grpc

from sky import sky_logging
from sky.schemas.generated import autostopv1_pb2
from sky.schemas.generated import autostopv1_pb2_grpc
from sky.skylet import autostop_lib

logger = sky_logging.init_logger(__name__)


class AutostopServiceImpl(autostopv1_pb2_grpc.AutostopServiceServicer):
    """Implementation of the AutostopService gRPC service."""

    def SetAutostop(  # type: ignore[return]
            self, request: autostopv1_pb2.SetAutostopRequest,
            context: grpc.ServicerContext
    ) -> autostopv1_pb2.SetAutostopResponse:
        """Sets autostop configuration for the cluster."""
        try:
            wait_for = autostop_lib.AutostopWaitFor.from_protobuf(
                request.wait_for)
            autostop_lib.set_autostop(
                idle_minutes=request.idle_minutes,
                backend=request.backend,
                wait_for=wait_for if wait_for is not None else
                autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR,
                down=request.down)
            return autostopv1_pb2.SetAutostopResponse()
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def IsAutostopping(  # type: ignore[return]
            self, request: autostopv1_pb2.IsAutostoppingRequest,
            context: grpc.ServicerContext
    ) -> autostopv1_pb2.IsAutostoppingResponse:
        """Checks if the cluster is currently autostopping."""
        try:
            is_autostopping = autostop_lib.get_is_autostopping()
            return autostopv1_pb2.IsAutostoppingResponse(
                is_autostopping=is_autostopping)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))
