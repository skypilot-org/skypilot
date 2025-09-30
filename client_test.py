import sky
import sky.client.sdk as sdk
import time

def get_node_info_latency():
    start_time = time.time()
    request_id = sdk.kubernetes_node_info()
    node_info = sky.get(request_id)
    assert node_info is not None
    end_time = time.time()
    return end_time - start_time

num_requests = 10
latencies = []
for i in range(num_requests):
    latency = get_node_info_latency()
    latencies.append(latency)
    print(f'Request {i} latency: {latency} seconds')

print(f'Average latency: {sum(latencies) / num_requests} seconds')