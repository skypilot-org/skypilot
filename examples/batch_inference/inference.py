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
        dialogs = [json.loads(d.strip()) for d in data]

    # Run inference
    batch_char_count = 0
    batch_messages = []
    batch_dialog_info = []
    predictions = []
    for i, dialog in enumerate(dialogs):
        conversation = dialog.pop('conversation')
        # Remove the last message in the conversation, to let our model to
        # generate the last response.
        # Conversation example:
        # [
        #   {'role': 'user', 'content': 'Hello, how are you?'},
        #   {'role': 'assistant', 'content': 'I am fine, thank you!'},
        #   {'role': 'user', 'content': 'What is your name?'}
        # ]
        conversation = conversation[:-1]
        # Calculate the word count of the conversation
        char_count = sum([len(message['content']) for message in conversation])
        batch_char_count += char_count

        if batch_char_count > BATCH_CHAR_COUNT:
            prediction = llm.chat(batch_messages, SAMPLING_PARAMS)
            for info, pred in zip(batch_dialog_info, prediction):
                info['prediction'] = pred
                predictions.append(info)
            batch_messages = []
            batch_dialog_info = []
            batch_char_count = 0

        batch_messages.append(conversation)
        batch_dialog_info.append(dialog)

    # Save predictions
    os.makedirs(OUTPUT_PATH, exist_ok=True)
    with open(os.path.join(OUTPUT_PATH, data_name), 'w') as f:
        for prediction in predictions:
            f.write(json.dumps(prediction) + '\n')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-name', type=str, required=True, help='The name of the model to be used for inference.')
    parser.add_argument('--data-path', type=str, required=True, help='The path to the data to be processed.')
    parser.add_argument('--num-gpus', type=int, required=True, help='The number of GPUs to be used for inference.')
    args = parser.parse_args()

    llm = LLM(args.model_name, tensor_parallel_size=args.num_gpus)

    batch_inference(llm, args.data_path)
