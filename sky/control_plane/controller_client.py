import logging

import grpc

from sky.control_plane.generated import controller_service, controller_messages

logger = logging.Logger(__name__)


class ControllerClient:

    def __init__(self, host: str = 'localhost', port=50051, timeout=10):
        channel = grpc.insecure_channel('{0}:{1}'.format(host, port))
        try:
            grpc.channel_ready_future(channel).result(timeout=10)
        except grpc.FutureTimeoutError as e:
            raise TimeoutError('Error connecting to server') from e
        self._stub = controller_service.ControllerStub(channel)
        self._timeout = timeout

    def launch(self, yaml_text: str) -> int:
        try:
            response = self._stub.Launch(
                controller_messages.LaunchRequest(yaml_text=yaml_text),
                timeout=self._timeout,
            )
        except grpc.RpcError as e:
            logger.error(f'Launch failed with {e.code()}')
            raise

        if not response.ok:
            raise logger.error('Launch failed')

        return response.job_id

    def get_logs(self, job_id: int, start=0, log_level=logging.INFO):
        try:
            request = controller_messages.GetLogsRequest(
                job_id=job_id,
                start=start,
                log_level=log_level,
            )
            response = self._stub.GetLogs(request, timeout=self._timeout)
        except grpc.RpcError as e:
            logger.error(f'Get logs failed with {e.code()}')
            raise

        try:
            for resp in response:
                yield resp
        except grpc.RpcError as e:
            logger.error(f'Streaming logs failed with {e.code()}')
            raise


if __name__ == '__main__':
    client = ControllerClient()

    for _ in range(10):
        job_id = client.launch(yaml_text='<yaml>')
        print(f'Job launched:  job_id={job_id}')
        for resp in client.get_logs(job_id):
            print(resp)
