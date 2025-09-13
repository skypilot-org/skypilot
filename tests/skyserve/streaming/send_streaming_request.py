import argparse
import time

import requests

with open('tests/skyserve/streaming/example.txt', 'r') as f:
    WORD_TO_STREAM = f.read()

parser = argparse.ArgumentParser()
parser.add_argument('--endpoint', type=str, required=True)
args = parser.parse_args()
url = f'{args.endpoint}/'

expected = WORD_TO_STREAM.split()
index = 0
max_attempts = 3

for attempt in range(max_attempts):
    try:
        with requests.get(url, stream=True) as response:
            if response.status_code == 503:
                # 503 Service Unavailable errors can occur when the load balancer has no available replicas.
                # As seen in the logs: "No replica selected for request" and "Available Replica URLs: []"
                # The availability of replicas can fluctuate during service startup and operation.
                # Implementing retries helps handle these temporary unavailability periods.
                print(
                    f"Retrying in 5 seconds... (attempt {attempt}/{max_attempts})"
                )
                time.sleep(5)
                continue
            else:
                response.raise_for_status()
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        current = chunk.decode().strip()
                        assert current == expected[index], (current,
                                                            expected[index])
                        index += 1
                break  # Success, exit the loop
    except requests.exceptions.ConnectionError:
        # EKS could have connection errors when the service is starting up.
        print(f"Retrying in 5 seconds... (attempt {attempt}/{max_attempts})")
        time.sleep(5)
        continue

assert index == len(expected)

print('Streaming test passed')
