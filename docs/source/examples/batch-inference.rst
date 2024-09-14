.. _offline-batch-inference:

Large-Scale Batch Inference
============================


Offline batch inference is a process for generating model predictions on a fixed set of input data. It is a common use case for AI:

* Large-scale document understanding
* Data pre-processing for training
* Synthetic data generation
* Scientific data analysis
* ...

SkyPilot enables large scale batch inference with a simple interface, offering the following benefits:

* Cost-effective: Pay only for the resources you use, and even cheaper spot instances.
* Faster: Scales out your jobs to multiple machines from any available resource pool.
* Robust: Automatically handles failures and recovers jobs.
* Easy to use: Abstracts away the complexity of distributed computing, giving you a simple interface to manage your jobs.
* Mounted Storage: Access data on object store as if they are local files.


Get Started with Single Node
----------------------------


Start a dev machine with dataset mounted.

.. code-block:: bash

    sky launch -c dev dev.yaml --workdir .
    ssh dev
    cd sky_workdir

.. note::

    To download the LMSys-1M dataset and split it into smaller chunks, you can run the following commands:

    .. code-block:: bash

        python download_and_convert.py


We have a bucket on R2 that contains the splitted LMSys-1M dataset. 

.. code-block:: python
    
    from vllm import LLM
    
    BATCH_CHAR_COUNT = 2000
    DATA_PATH = '/data/part_0.jsonl'
    OUTPUT_PATH = '/output'

    llm = LLM(model='meta-llama/Meta-Llama-3.1-7B-Instruct', tensor_parallel_size=1)

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
    
    batch_inference(llm, data_path)

Or, you can try it with:

.. code-block:: bash

    python inference.py \
      --model-name meta-llama/Meta-Llama-3.1-7B-Instruct \
      --num-gpus 1 \
      --data-chunk-file /data/part_0.jsonl


Scale out to Multiple Nodes
---------------------------

Chunk your data into multiple pieces to leverage fully distributed batch inference on multiple machines.

.. code-block:: python

    NUM_CHUNKS = 10

    def chunk_data(data_paths: str, num_chunks: int):
        # Chunk data paths in to multiple chunks
        data_chunks = []
        chunk_size = len(data_paths) // num_chunks
        for i in range(num_chunks):
            data_chunks.append(data_paths[i * chunk_size:(i + 1) * chunk_size])
        return data_chunks

    data_chunks = chunk_data(data_paths, NUM_CHUNKS)

    # Save data chunks to different files
    for i, data_chunk in enumerate(data_chunks):
        with open(f'./chunks/{i}.txt', 'w') as f:
            f.write('\n'.join(data_chunk))

We can use the chunk script to chunk data in LMSys Chat Dataset.

.. code-block:: bash

    python chunk.py \
      --data-paths-file ./metadata.txt \
      --num-chunks 16
            
With the data chunks saved, we can launch a job for each chunk.

.. code-block:: bash

    # Launch a job for each chunk
    NUM_CHUNKS=16
    for i in $(seq 0 $((NUM_CHUNKS - 1))); do
        sky jobs launch -y -d -n chunk-$i worker.yaml \
          --env DATA_CHUNK_FILE=./chunks/$i.txt
    done


Cut Costs by 3x with Spot Instances
-----------------------------------


.. code-block:: python

    def continue_batch_inference(data_paths: List[str], output_path: str):
        # Automatically skip processed data, resume the rest.
        for data_path in data_paths:
            data_name = data_path.split('/')[-1]
            succeed_indicator = os.path.join(output_path, data_name + '.succeed')
            if os.path.exists(succeed_indicator):
                print(f'Skipping {data_path} because it has been processed.')
                continue

            prediction = batch_inference(data_path, output_path)

            save_prediction(prediction, output_path)
            mark_as_done(succeed_indicator)


.. code-block:: bash

    # Use spot instances to reduce costs
    NUM_CHUNKS=10
    for i in $(seq 0 $((NUM_CHUNKS - 1))); do
        sky jobs launch -y -n chunk-$i worker.yaml \
          --env DATA_CHUNK_FILE=./chunks/$i.txt \
          --use-spot
    done


Online Batch Inference
----------------------

# TODO: whether to include this section with a queue


Advance Tips
------------

1. Data Placement: To avoid expensive data egress costs, you can place your input data on Cloudflare R2,
which does not charge for data egress, so you don't need to pay for the data reading.

TODO: how to deal with output data?

2. Chunk Size: 

3. 










