import os
import sys

sys.path.append(os.path.dirname(__file__))

# TODO(suquark): This is a temporary hack. We should compile gRPC protobuf
#  during setup.
try:
    from sky.control_plane.generated import controller_pb2_grpc as controller_service
    from sky.control_plane.generated import controller_pb2 as controller_messages
except ImportError:
    workdir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    os.system(os.path.join(workdir, 'scripts', 'build.sh'))

    from sky.control_plane.generated import controller_pb2_grpc as controller_service
    from sky.control_plane.generated import controller_pb2 as controller_messages

__all__ = ('controller_service', 'controller_messages')
