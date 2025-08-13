import argparse

import requests

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SkyServe Smoke Test Client')
    parser.add_argument('--endpoint', type=str, required=True)
    parser.add_argument('--prompt', type=str, required=True)
    parser.add_argument('--auth_token', type=str, required=True)
    args = parser.parse_args()

    messages = [
        {
            'role': 'system',
            'content': 'You are a helpful assistant.'
        },
        {
            'role': 'user',
            'content': args.prompt
        },
    ]

    url = f'{args.endpoint}/v1/chat/completions'
    resp = requests.post(url,
                         json={
                             'model': 'Qwen/Qwen3-0.6B',
                             'messages': messages,
                             'temperature': 0,
                         },
                         headers={'Authorization': f'Bearer {args.auth_token}'},
                         timeout=30)

    if resp.status_code != 200:
        error_msg = (f'Failed to get response: {resp.content.decode("utf-8")}\n'
                     f'Status code: {resp.status_code}\n'
                     f'Request URL: {url}\n'
                     f'Request headers: {resp.request.headers}\n'
                     f'Request body: {resp.text}')
        raise RuntimeError(error_msg)
    print(resp.json()['choices'][0]['message']['content'])
