.. _offline-batch-inference:

Large-Scale Offline Batch Inference
===================================


Offline batch inference is a process for generating model predictions on a fixed set of input data. By batching multiple inputs into a single request, you can significantly improve the GPU utilization and reduce the total inference cost.

It is a common use case for AI:

* Large-scale document processing (summarization, entity retreival, etc)
* Data pre-processing for training
* Synthetic data generation
* Scientific data analysis
* ...

.. SkyPilot enables large scale batch inference with a simple interface, offering the following benefits:

.. * Cost-effective: Pay only for the resources you use, and even cheaper spot instances.
.. * Faster: Scales out your jobs to multiple machines from any available resource pool.
.. * Robust: Automatically handles failures and recovers jobs.
.. * Easy to use: Abstracts away the complexity of distributed computing, giving you a simple interface to manage your jobs.
.. * Mounted Storage: Access data on object store as if they are local files.

We now walk through the steps for developing and scaling out batch inference with SkyPilot.
We take a real-world example, LMSys-1M dataset, and a popular open-source model, Meta-Llama-3.1-7B, to showcase the process.
All scripts can be found in the `examples in SkyPilot repository <https://github.com/skypilot-org/skypilot/tree/main/examples/batch_inference>`__.

**TL;DR:** To run batch inference on LMSys-1M dataset with Meta-Llama-3.1-7B model, you can use the following command:

.. code-block:: bash

    # Clone SkyPilot repository and cd into the examples/batch_inference directory.
    git clone https://github.com/skypilot-org/skypilot.git
    cd skypilot/examples/batch_inference

    # Step 0: Create dev machine and preprocess data.
    sky launch -c dev dev.yaml --workdir .
    ssh dev
    cd sky_workdir

    # Step 1: Download data from LMSys-1M dataset, and convert it into 200 smaller chunks.
    python utils/download_and_convert.py --num-chunks 200

    # Step 2: Spread the chunks among workers.
    NUM_WORKERS=16
    python utils/get_per_worker_data.py \
        --data-metadata metadata.txt \
        --num-workers $NUM_WORKERS

    # Step 3: Launch workers and process the data.
    for i in $(seq 0 $((NUM_WORKERS - 1))); do
        sky jobs launch -y -n worker-$i worker.yaml \
          --env DATA_METADATA=./workers/$i.txt \
          --env MODEL_NAME=meta-llama/Meta-Llama-3.1-8B-Instruct &
    done

.. TODO: Add an image for the `sky jobs queue`

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

    This step is optional. We offer a R2 bucket with LMSys-1M dataset that is already divided into smaller chunks: ``r2://skypilot-lmsys-chat-1m`` (Check it in browser `here <https://pub-109f99b93eac4c22939d0ed4385f0dcd.r2.dev>`_).

.. TODO: confirm r2 bucket's public access

We first start a machine for development. The following command will start a small CPU machine with the current directory synced and a R2 bucket mounted, so we can directly operate on the data as if they are on local disk.

.. code-block:: bash

    # Clone SkyPilot repository and cd into the examples/batch_inference directory.
    git clone https://github.com/skypilot-org/skypilot.git
    cd skypilot/examples/batch_inference

    # Launch a dev machine.
    sky launch -c dev dev.yaml --workdir .
    # SSH into the remote machine.
    ssh dev
    # Running on remote machine.
    cd sky_workdir

On the remote dev machine, we download the LMSys-1M dataset and split it into smaller chunks, using a utility script:

.. code-block:: bash

    python utils/download_and_convert.py --num-chunks 200

This script converts the dataset into 200 chunks, each containing 5000 conversations. A metadata file is also generated with paths to chunk files:

.. code-block::
  
    # metadata.txt
    part_0.jsonl
    part_1.jsonl
    ...
    part_199.jsonl

.. note::

    We use R2 bucket as it has no data egress fee, so we can easily scale out the inference to multiple regions/clouds without additional costs for data reading.


.. _develop-inference-script:

Develop Inference Script
------------------------

We now develop an inference script to generate predictions for each chunk.

First of all, we can start another dev machine with GPUs to interactively develop and debug the inference script.

.. code-block:: bash

    sky launch -c dev-gpu dev.yaml --gpus L4 --workdir .
    ssh dev-gpu
    cd sky_workdir

We now develop the inference script to generate predictions with the first turn of each conversation in LMSys-1M dataset. 

The following is an example script, where we process a chunk of data *batch by batch*:

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

For complete script, see `examples/batch_inference/inference.py <https://github.com/skypilot-org/skypilot/blob/main/examples/batch_inference/inference.py>`__ and you can run it with ``HF_TOKEN=<your-huggingface-token> python inference.py`` to test it on the dev machine.

After testing it on the dev machine, we can now compose a new yaml (`inference.yaml <https://github.com/skypilot-org/skypilot/blob/main/examples/batch_inference/inference.yaml>`) to run the inference on clouds.

.. code-block:: bash

    # Set HuggingFace token for accessing Llama model weights.
    export HF_TOKEN=...
    sky launch -c inf ./inference.yaml \
        --env HF_TOKEN

.. TODO: make r2 bucket publically accessible
.. tested with inference.py and inference.yaml on 2024-09-15 and works well.

.. _scale-out-to-multiple-nodes:

Scale Out to Multiple Nodes
---------------------------

To scale out the inference to multiple machines, we can spread data chunks among multiple workers so that each worker can process a subset of data chunks.

The following script (`utils/get_per_worker_data.py <https://github.com/skypilot-org/skypilot/blob/main/examples/batch_inference/utils/get_per_worker_data.py>`_) reads the metadata file and splits the paths of data chunks for each worker. 

.. code-block:: python

    def get_per_worker_chunk_paths(chunk_paths: List[str], num_workers: int) -> List[List[str]]:
        # Spread data paths among workers
        per_worker_chunk_paths = []
        per_worker_num_chunks = len(chunk_paths) // num_workers
        for i in range(num_workers):
            per_worker_chunk_paths.append(chunk_paths[i * per_worker_num_chunks:(i + 1) * per_worker_num_chunks])
        return per_worker_chunk_paths

    per_worker_chunk_paths = get_per_worker_chunk_paths(chunk_paths, num_workers)

    # Save data chunks to different files
    for i, worker_chunk_paths in enumerate(per_worker_chunk_paths):
        with open(f'./workers/{i}.txt', 'w') as f:
            f.write('\n'.join(worker_chunk_paths))


.. code-block::

    # ./workers/0.txt
    part_0.jsonl
    part_1.jsonl
    ...
    part_13.jsonl

On dev machine, we can use the ``get_per_worker_data.py`` script to split data chunks into the subsets for each worker.

.. code-block:: bash

    python utils/get_per_worker_data.py \
      --data-metadata ./metadata.txt \
      --num-workers 16
            
After that, we can launch a job for each worker to process the subsets of data chunks in parallel.

.. code-block:: bash

    # Launch a job for each worker
    NUM_WORKERS=16
    for i in $(seq 0 $((NUM_WORKERS - 1))); do
        # We use & to launch jobs in parallel
        sky jobs launch -y -d -n worker-$i worker.yaml \
          --env DATA_METADATA=./workers/$i.txt &
    done

.. Tested worker on 2024-09-15 with a worker containing multiple data parts.

Cut Costs by ~5x with Spot Instances and Specialized AI Clouds
--------------------------------------------------------------

Batch inference can get pretty expensive when it involves large models and high-end
GPUs. By leveraging spot instances and specialized clouds, you should achieve around
5x cost reduction by giving away some robustness guarantee.

To handle the robustness issue, we can wrap our batch inference code to resume
batch inference during the event of spot preemption or node/GPU failure.

The following code, checks the completed chunks and continue the unfinished chunks
whenever a failure happens.

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

To allow SkyPilot searching through all available spot instances and specialized
AI clouds with different accelerators based on cost, we add the following fields
in the ``worker.yaml``. It allows SkyPilot to search for the cheapest resources,
among different accelerator types, including L4, L40, etc, with different pricing
models, including on-demand and spot instances, on all enabled cloud providers.

.. code-block:: yaml

    resources:
        accelerators: {L4, L40, A10, A10g, A100, A100-80GB}
        any_of:
            - use_spot: true
            - use_spot: false

We then start the batch inference workers with the same script:

.. code-block:: bash
    
    # Use spot instances to reduce costs
    NUM_WORKERS=16
    HF_TOKEN=...
    for i in $(seq 0 $((NUM_WORKERS - 1))); do
        sky jobs launch -y -n worker-$i worker.yaml \
          --env DATA_METADATA=./workers/$i.txt \
          --env OUTPUT_BUCKET=my-output-bucket \
          --env HF_TOKEN &
    done

.. Tested worker on 2024-09-15 with continue_batch_inference.


Advance Tips
------------

1. Data placement: To avoid expensive data egress costs, you can place your input data on Cloudflare R2,
which does not charge for data egress, so you don't need to pay for the data reading.

.. TODO: how to deal with output data?

2. Reduce restart overhead: Keeping the average overhead (including provisioning, setting up and potential progress loss during failure)
to be within half an hour could be ideal for more efficient usage of spot instances, according to our `paper <https://www.usenix.org/conference/nsdi24/presentation/wu-zhanghao>`__.

3. Chunk size: the time for processing a data chunk is highly related to the size (number of samples) within a chunk, which will impact the potential progress loss during failure as mentioned in *Tip 2*. Before splitting the dataset into chunks, you could benchmark the time for
processing a single chunk in order to get the best performance.


Next steps
----------

1. Details of :ref:`SkyPilot Manged Jobs <managed-jobs>`.
2. Join `SkyPilot community Slack <https://slack.skypilot.co>`__ for questions and requests.

