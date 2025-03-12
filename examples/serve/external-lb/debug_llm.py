import json
import time
import traceback

import requests

stream = False
endpoint = input('Endpoint: ')
model = 'meta-llama/Llama-3.2-3B-Instruct'
init_prompt = 'You are a helpful assistant.'
history = [{'role': 'system', 'content': init_prompt}]
url = f'http://{endpoint}/v1/chat/completions'

if stream:
    import openai
    client = openai.OpenAI(
        api_key='placeholder',
        base_url=f'http://{endpoint}/v1',
    )

try:
    i = 0
    start = time.time()
    while True:
        # user_input = input('[User] ')
        user_input = 'hi'
        history.append({'role': 'user', 'content': user_input})
        if stream:
            resp = client.chat.completions.create(
                model=model,
                messages=history,
                stream=True,
            )
            print('[Chatbot]', end='', flush=True)
            tot = ''
            for i in resp:
                dlt = i.choices[0].delta.content
                print(dlt, end='', flush=True)
                tot += dlt
            print()
            history.append({'role': 'assistant', 'content': tot})
        else:
            data = {'model': model, 'messages': history, 'temperature': 0.0}
            resp = requests.post(url, data=json.dumps(data))
            resp_json = resp.json()
            msg = resp_json['choices'][0]['message']
            msg.pop('tool_calls')
            print(f'[Chatbot {i}]', msg['content'])
            print(f'Time taken: {time.time() - start}')
            history.append(msg)
            i += 1
except KeyboardInterrupt:
    print('\nBye!')
except Exception as e:
    print(f"\nError occurred: {e}")
    traceback.print_exc()
    print(resp_json)
