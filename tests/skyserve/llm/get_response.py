import argparse

import requests

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SkyServe Smoke Test Client')
    parser.add_argument('--endpoint', type=str, required=True)
    parser.add_argument('--prompt', type=str, required=True)
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

    url = f'http://{args.endpoint}/v1/chat/completions'
    resp = requests.post(url,
                         json={
                             'model': 'fastchat-t5-3b-v1.0',
                             'messages': messages,
                             'temperature': 0,
                         })

    print(resp.json()['choices'][0]['message']['content'])
