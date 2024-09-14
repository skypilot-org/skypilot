import argparse
import json
import os

from vllm import LLM, SamplingParams

DATA_PATH = '/data'
OUTPUT_PATH = '/output'

SAMPLING_PARAMS = SamplingParams(temperature=0.2,
                                 max_tokens=100,
                                 min_p=0.15,
                                 top_p=0.85)
BATCH_CHAR_COUNT = 2000

def batch_inference(llm: LLM, data_path: str):
    print(f'Processing {data_path}...')
    data_name = data_path.split('/')[-1]

    # Read data (jsonl), each line is a json object
    with open(data_path, 'r') as f:
        data = f.readlines()
        # Extract the first message from the conversation
        messages = [json.loads(d.strip())['conversation'][0]['content'] for d in data]

    # Run inference
    batch_char_count = 0
    batch_messages = []
    generated_text = []
    for message in messages:
        # Calculate the word count of the conversation
        char_count = len(message)
        batch_char_count += char_count

        if batch_char_count > BATCH_CHAR_COUNT:
            outputs = llm.generate(batch_messages, SAMPLING_PARAMS)
            generated_text = []
            for output in outputs:
                generated_text.append(' '.join([o.text for o in output.outputs]))
            batch_messages = []
            batch_char_count = 0

        batch_messages.append(message)

    # Save predictions
    os.makedirs(OUTPUT_PATH, exist_ok=True)
    with open(os.path.join(OUTPUT_PATH, data_name), 'w') as f:
        for text in generated_text:
            f.write(text + '\n')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-name', type=str, required=True, help='The name of the model to be used for inference.')
    parser.add_argument('--data-path', type=str, required=True, help='The path to the data to be processed.')
    parser.add_argument('--num-gpus', type=int, required=True, help='The number of GPUs to be used for inference.')
    args = parser.parse_args()

    llm = LLM(args.model_name, tensor_parallel_size=args.num_gpus, max_model_len=10240)

    batch_inference(llm, args.data_path)
