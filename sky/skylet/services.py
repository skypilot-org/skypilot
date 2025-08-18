"""gRPC service implementations for skylet."""

import grpc

from sky import sky_logging
from sky.schemas.generated import autostopv1_pb2
from sky.schemas.generated import autostopv1_pb2_grpc
from sky.schemas.generated import servev1_pb2
from sky.schemas.generated import servev1_pb2_grpc
from sky.serve import serve_rpc_utils
from sky.serve import serve_state
from sky.serve import serve_utils
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


class ServeServiceImpl(servev1_pb2_grpc.ServeServiceServicer):
    """Implementation of the ServeService gRPC service."""

    # NOTE (kyuds): this grpc service will run cluster-side,
    # thus guaranteeing that SERVE_VERSION is above 5.
    # Therefore, we are checking some SERVE_VERSION checks
    # present in the original codegen. 

    def GetServiceStatus(  # type: ignore[return]
            self, request: servev1_pb2.GetServiceStatusRequest,
            context: grpc.ServicerContext
    ) -> servev1_pb2.GetServiceStatusResponse:
        """Gets serve status."""
        try:
            service_names, pool = (
                serve_rpc_utils.GetServiceStatusRequestConverter.from_proto(request))  # pylint: disable=line-too-long
            statuses = serve_utils.get_service_status_pickled(
                service_names, pool)
            return serve_rpc_utils.GetServiceStatusResponseConverter.to_proto(
                statuses)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def AddVersion(  # type: ignore[return]
            self, request: servev1_pb2.AddVersionRequest,
            context: grpc.ServicerContext) -> servev1_pb2.AddVersionResponse:
        """Adds serve version"""
        try:
            service_name = request.service_name
            version = serve_state.add_version(service_name)
            return servev1_pb2.AddVersionResponse(version=version)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def TerminateServices(  # type: ignore[return]
            self, request: servev1_pb2.TerminateServiceRequest,
            context: grpc.ServicerContext
    ) -> servev1_pb2.TerminateServiceResponse:
        """Terminates serve"""
        try:
            service_names, purge, pool = (
                serve_rpc_utils.TerminateServiceRequestConverter.from_proto(request))  # pylint: disable=line-too-long
            message = serve_utils.terminate_services(service_names,
                                                     purge,
                                                     pool)
            return servev1_pb2.TerminateServiceResponse(message=message)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    def TerminateReplica(  # type: ignore[return]
            self, request: servev1_pb2.TerminateReplicaRequest,
            context: grpc.ServicerContext) -> servev1_pb2.TerminateReplicaResponse:
        """Terminate replica"""
        try:
            service_name = request.service_name
            replica_id = request.replica_id
            purge = request.purge
            message = serve_utils.terminate_replica(service_name, replica_id, purge)
            return servev1_pb2.TerminateReplicaResponse(message=message)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def WaitServiceRegistration(  # type: ignore[return]
            self, request: servev1_pb2.WaitRegistrationRequest,
            context: grpc.ServicerContext) -> servev1_pb2.WaitRegistrationResponse:
        """Wait for service to be registered"""
        try:
            service_name = request.service_name
            job_id = request.job_id
            pool = request.pool
            # TODO (kyuds): to maintain backwards compatibility, we currently
            # encode the load balancer port with message_utils. This is why
            # we need a "unnecessary" decoding step. When codegen is fully
            # deprecated, return the lb port int directly.
            encoded = serve_utils.wait_service_registration(service_name, job_id, pool)
            lb_port = serve_utils.load_service_initialization_result(encoded)
            return servev1_pb2.WaitRegistrationResponse(lb_port=lb_port)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def UpdateService(  # type: ignore[return]
            self, request: servev1_pb2.UpdateServiceRequest,
            context: grpc.ServicerContext) -> servev1_pb2.UpdateServiceResponse:
        """Update service"""
        try:
            service_name = request.service_name
            version = request.version
            mode = request.mode
            pool = request.pool
            encoded_message = serve_utils.update_service_encoded(service_name, version, mode, pool)
            return servev1_pb2.UpdateServiceResponse(encoded_message=encoded_message)
        except Exception as e:  # pylint: disable=broad-except
            context.abort(grpc.StatusCode.INTERNAL, str(e))
