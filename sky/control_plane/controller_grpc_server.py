from concurrent import futures
import time

import grpc

from sky.control_plane import utils
from sky.control_plane.generated import controller_service, controller_messages

_ONE_DAY_IN_SECONDS = 60 * 60 * 24

global_job_id = utils.FilelockMonotonicID('/tmp/sky_job_id.lock')


class ControllerService(controller_service.ControllerServicer):

    def Launch(self, request: controller_messages.LaunchRequest, context):
        metadata = dict(context.invocation_metadata())
        print(metadata, request.yaml_text)
        return controller_messages.LaunchResult(ok=True,
                                                job_id=global_job_id.next_id())

    def GetLogs(self, request: controller_messages.GetLogsRequest, context):
        for _ in range(3):
            yield controller_messages.Log(timestamp=int(time.time() * 10**6),
                                          log_level=request.log_level,
                                          text='<log text>')


def serve(address: str):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    controller_service.add_ControllerServicer_to_server(ControllerService(),
                                                        server)
    server.add_insecure_port(address)
    server.start()
    print(f'Server ready. Address = {address}')
    try:
        while True:
            time.sleep(_ONE_DAY_IN_SECONDS)
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == '__main__':
    serve('0.0.0.0:50051')
