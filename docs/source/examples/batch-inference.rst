.. _offline-batch-inference:

Large-Scale Batch Inference
============================


Offline batch inference is a process for generating model predictions on a fixed set of input data. By batching multiple inputs into a single request, we can significantly improve the GPU utilization and reduce the inference cost.

It is a common use case for AI:

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

We now walk through the steps for developing and scaling out batch inference. We take a real-world example, LMSys-1M dataset, and the popular open-source model, Meta-Llama-3.1-7B, to showcase the process.

TL;DR: To run batch inference on LMSys-1M dataset with Meta-Llama-3.1-7B model, you can use the following command:

.. code-block:: bash

    NUM_PARALLEL_GROUPS=16
    for i in $(seq 0 $((NUM_PARALLEL_GROUPS - 1))); do
        sky jobs launch -y -n group-$i worker.yaml \
          --env DATA_GROUP_METADATA=./groups/$i.txt
    done

.. _split-data-into-smaller-chunks:

Split Data into Smaller Chunks
------------------------------


[LMSys-1M dataset](https://huggingface.co/datasets/lmsys/lmsys-chat-1m) contains 1M conversations with 6 parquet files:

.. code-block::

    train-00000-of-00006-*.parquet
    train-00001-of-00006-*.parquet
    train-00002-of-00006-*.parquet
    train-00003-of-00006-*.parquet
    train-00004-of-00006-*.parquet
    train-00005-of-00006-*.parquet


For simplicity, in order to scale out the inference to more than 6 nodes, we can firstly split the entire dataset into smaller chunks.

.. note::

    This step is optional, as we have a bucket on R2 that contains the splitted LMSys-1M dataset already: ``r2://skypilot-lmsys-chat-1m`` (Check it in browser `here <https://pub-109f99b93eac4c22939d0ed4385f0dcd.r2.dev>`_).

.. TODO: confirm r2 bucket's public access

We first start a dev machine to split the data into smaller chunks. The following command will start a small CPU machine with the current directory synced and a R2 bucket mounted.

.. code-block:: bash

    sky launch -c dev dev.yaml --workdir .
    ssh dev
    cd sky_workdir

On the remote dev machine, we download the LMSys-1M dataset and split it into smaller chunks.

.. code-block:: bash

    python download_and_convert.py

This script converts the dataset into 200 chunks of jsonl files, each containing 5000 conversations, with a metadata file containing all the paths to the data chunks:

.. code-block::
  
    part_0.jsonl
    part_1.jsonl
    ...
    part_199.jsonl

.. note::

    We use R2 bucket as it does not charge for data egress, so we can easily scale out the inference to multiple regions/clouds without additional costs for data reading.


.. _develop-inference-script:

Develop Inference Script
------------------------

With the data splitted into smaller chunks, we can now develop an inference script to generate predictions for each chunk.

First of all, we start another dev machine with GPUs so we can develop and debug the inference script by directly logging into the machine and running the script.

.. code-block:: bash

    sky launch -c dev dev.yaml --gpus L4 --workdir .
    ssh dev
    cd sky_workdir

We now develop the inference script to generate predictions for the first turn of each conversation in LMSys-1M dataset. 

The following is an example script, where we aggregate multiple inputs into a single batch for better GPU utilization, and process the entire chunk of data batch by batch:

.. code-block:: python
    
    from vllm import LLM
    
    BATCH_CHAR_COUNT = 2000
    DATA_PATH = '/data/part_0.jsonl'
    OUTPUT_PATH = '/output'

    llm = LLM(model='meta-llama/Meta-Llama-3.1-7B-Instruct', tensor_parallel_size=1)

    def batch_inference(llm: LLM, data_path: str):
        # This can take about 1-2 hours on a L4 GPU.
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
        for message in tqdm(messages):
            # Calculate the word count of the conversation
            char_count = len(message)
            batch_char_count += char_count

            if batch_char_count > BATCH_CHAR_COUNT:
                outputs = llm.generate(batch_messages, SAMPLING_PARAMS, use_tqdm=False)
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

    batch_inference(llm, DATA_PATH)

For complete script, see `examples/batch_inference/inference.py <https://github.com/skypilot-org/skypilot/blob/main/examples/batch_inference/inference.py>`_ and you can run it with ``HF_TOKEN=<your-huggingface-token> python inference.py`` to test it on the dev machine.

After testing it on the dev machine, we can now compose a task yaml (`inference.yaml <https://github.com/skypilot-org/skypilot/blob/main/examples/batch_inference/inference.yaml>`) to run the inference on clouds.

.. code-block:: bash

    # Set HuggingFace token for accessing Llama model weights.
    export HF_TOKEN=...
    sky launch -c inf ./inference.yaml \
        --env HF_TOKEN

.. TODO: make r2 bucket publically accessible
.. tested with inference.py and inference.yaml on 2024-09-15 and works well.

.. _scale-out-to-multiple-nodes:

Scale out to Multiple Nodes
---------------------------

To scale out the inference to multiple machines, we can group the data chunks into multiple pieces so that each machine can process one piece.

The following script (`group_data.py <https://github.com/skypilot-org/skypilot/blob/main/examples/batch_inference/group_data.py>`_) reads the metadata file and splits the path of data chunks into multiple groups. 

.. code-block:: python

    NUM_GROUPS = 16

    def group_data(data_paths: str, num_groups: int):
        # Chunk data paths in to multiple groups
        data_groups = []
        group_size = len(data_paths) // num_groups
        for i in range(num_groups):
            data_groups.append(data_paths[i * group_size:(i + 1) * group_size])
        return data_groups

    data_groups = chunk_data(data_paths, NUM_GROUPS)

    # Save data chunks to different files
    for i, data_group in enumerate(data_groups):
        with open(f'./groups/{i}.txt', 'w') as f:
            f.write('\n'.join(data_group))


.. code-block::

    # ./groups/0.txt
    part_0.jsonl
    part_1.jsonl
    ...
    part_13.jsonl

On dev machine, we can use the ``group_data.py`` script to group data chunks into the number of machines we want to scale out.

.. code-block:: bash

    python group_data.py \
      --data-metadata ./metadata.txt \
      --num-groups 16
            
After that, we can launch a job for each group to process the groups in parallel.

.. code-block:: bash

    # Launch a job for each chunk
    NUM_CHUNKS=16
    for i in $(seq 0 $((NUM_CHUNKS - 1))); do
        # We use & to launch jobs in parallel
        sky jobs launch -y -d -n group-$i worker.yaml \
          --env DATA_GROUP_METADATA=./groups/$i.txt &
    done

.. Tested worker on 2024-09-15 with a group containing multiple data parts.

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

.. Tested worker on 2024-09-15 with continue_batch_inference.

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










