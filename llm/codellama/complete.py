import openai

import sky
from sky import serve

service_records = sky.serve.status('code-llama')
endpoint = serve.get_endpoint(service_records[0])

print('Using endpoint:', endpoint)

client = openai.OpenAI(
    base_url=f'http://{endpoint}/v1',
    # No API key is required when self-hosted.
    api_key='EMPTY')

chat_completion = client.chat.completions.create(
    model='codellama/CodeLlama-70b-Instruct-hf',
    messages=[{
        'role': 'system',
        'content': 'You are a helpful and honest code assistant expert in Python.'
    }, {
        'role': 'user',
        'content': 'Show me the code for quick sort a list of integers.'
    }],
    max_tokens=300,
)

print(chat_completion.model_dump())
