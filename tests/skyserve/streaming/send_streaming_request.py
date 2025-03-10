import argparse

import requests

with open('tests/skyserve/streaming/example.txt', 'r') as f:
    WORD_TO_STREAM = f.read()

parser = argparse.ArgumentParser()
parser.add_argument('--endpoint', type=str, required=True)
args = parser.parse_args()
url = f'{args.endpoint}/'

expected = WORD_TO_STREAM.split()
index = 0
max_retries = 3
retry_count = 0

while retry_count < max_retries:
    try:
        with requests.get(url, stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    current = chunk.decode().strip()
                    assert current == expected[index], (current,
                                                        expected[index])
                    index += 1
            break  # Success, exit the loop
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 503:
            retry_count += 1
            if retry_count < max_retries:
                print(
                    f"Got 503 Server Error, retrying in 5 seconds... (attempt {retry_count}/{max_retries})"
                )
                import time
                time.sleep(5)
            else:
                print(f"Max retries reached after {max_retries} attempts")
                raise
        else:
            # For other HTTP errors, raise immediately
            raise

assert index == len(expected)

print('Streaming test passed')
