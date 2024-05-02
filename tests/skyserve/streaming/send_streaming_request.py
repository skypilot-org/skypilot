import argparse

import requests

with open('tests/skyserve/streaming/example.txt', 'r') as f:
    WORD_TO_STREAM = f.read()

parser = argparse.ArgumentParser()
parser.add_argument('--endpoint', type=str, required=True)
args = parser.parse_args()
url = f'http://{args.endpoint}/'

expected = WORD_TO_STREAM.split()
index = 0
with requests.get(url, stream=True) as response:
    response.raise_for_status()
    for chunk in response.iter_content(chunk_size=8192):
        if chunk:
            current = chunk.decode().strip()
            assert current == expected[index], (current, expected[index])
            index += 1
assert index == len(expected)

print('Streaming test passed')
