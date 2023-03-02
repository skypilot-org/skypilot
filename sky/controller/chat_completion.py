# Note: you need to be using OpenAI Python v0.27.0 for the code below to work
import os
import openai

openai.api_key_path = os.path.expanduser('~/.openai')


def chat(dialogues: list):
    with open(os.path.dirname(__file__) + '/skypilot_prompt.txt') as f:
        skypilot_prompt = f.read()

    response = openai.ChatCompletion.create(
        model='gpt-3.5-turbo',
        messages=[{
            'role': 'system',
            'content': 'You are a helpful assistant.'
        }, {
            'role': 'user',
            'content': skypilot_prompt
        }, {
            'role': 'assistant',
            'content': 'I understand.'
        }] + dialogues)

    return response['choices'][0]['message']['content']


if __name__ == '__main__':
    print(
        chat(
            'give me a AWS cpu cluster with two nodes, with name "test-cluster"'
        ))
