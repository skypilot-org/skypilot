import json

import openai
import requests

stream = True
model = 'Llama-2-7b-chat-hf'
init_prompt = 'You are a helpful assistant.'
history = [{'role': 'system', 'content': init_prompt}]
endpoint = input('Endpoint: ')
url = f'http://{endpoint}/v1/chat/completions'
openai.api_base = f'http://{endpoint}/v1'
openai.api_key = 'placeholder'

try:
    while True:
        user_input = input('[User] ')
        history.append({'role': 'user', 'content': user_input})
        if stream:
            resp = openai.ChatCompletion.create(model=model,
                                                messages=history,
                                                stream=True)
            print('[Chatbot]', end='', flush=True)
            tot = ''
            for i in resp:
                dlt = i['choices'][0]['delta']
                if 'content' not in dlt:
                    continue
                print(dlt['content'], end='', flush=True)
                tot += dlt['content']
            print()
            history.append({'role': 'assistant', 'content': tot})
        else:
            resp = requests.post(url,
                                 data=json.dumps({
                                     'model': model,
                                     'messages': history
                                 }))
            msg = resp.json()['choices'][0]['message']
            print('[Chatbot]' + msg['content'])
            history.append(msg)
except KeyboardInterrupt:
    print('\nBye!')
