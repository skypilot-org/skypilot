# Retrieval Augmented Generation with DeepSeek R1

<p align="center">
<img src="https://i.imgur.com/yysdtA4.png" alt="RAG with DeepSeek R1" style="width: 70%;">
</p>

For the full blog post, please find it [here](https://blog.skypilot.co/deepseek-rag/).

## Large-Scale Legal Document Search and Analysis
As legal document collections grow, traditional keyword search becomes insufficient for finding relevant information. Retrieval-Augmented Generation (RAG) combines the power of vector search with large language models to enable semantic search and intelligent answers.

In particular:

* **Accuracy**: RAG systems ground their responses in source documents, reducing hallucination and improving answer reliability.
* **Context-Awareness**: By retrieving relevant documents before generating answers, the system provides responses that consider specific legal contexts.
* **Traceability**: All generated answers can be traced back to source documents, crucial for legal applications.


SkyPilot streamlines the development and deployment of RAG systems in any cloud or kubernetes by managing infrastructure and enabling efficient, cost-effective compute resource usage.

In this example, we use legal documents by [pile of law](https://huggingface.co/datasets/pile-of-law/pile-of-law) as example data to demonstrate RAG capabilities. The system processes a collection of legal texts, including case law, statutes, and legal discussions, to enable semantic search and intelligent question answering. This approach can help legal professionals quickly find relevant precedents, analyze complex legal scenarios, and extract insights from large document collections. 

We use [Alibaba-NLP/gte-Qwen2-7B-instruct](https://huggingface.co/Alibaba-NLP/gte-Qwen2-7B-instruct) for generating document embeddings and distilled Deepseek R1 ([deepseek-ai/DeepSeek-R1-Distill-Llama-8B](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Llama-8B)) for generating final anwsers. 

**Why SkyPilot**: SkyPilot streamlines the process of running such large-scale jobs in the cloud. It abstracts away much of the complexity of managing infrastructure and helps you run compute-intensive tasks efficiently and cost-effectively through managed jobs. 

## Step 0: Set Up The Environment
Install the following Prerequisites:  
* SkyPilot: Ensure SkyPilot is installed and `sky check` succeeds. See [installation instructions](https://docs.skypilot.co/en/latest/getting-started/installation.html).

Set up bucket names for storing embeddings and vector database:
```bash
export EMBEDDINGS_BUCKET_NAME=sky-rag-embeddings
export VECTORDB_BUCKET_NAME=sky-rag-vectordb
```
Note that these bucket names need to be unique to the entire SkyPilot community. 


## Step 1: Compute Embeddings from Legal Documents
Convert legal documents into vector representations using `Alibaba-NLP/gte-Qwen2-7B-instruct`. These embeddings enable semantic search across the document collection.

Launch the embedding computation:
```bash
python3 batch_compute_embeddings.py --embedding_bucket_name $EMBEDDINGS_BUCKET_NAME
```

Here is how the python script launches vLLM with `Alibaba-NLP/gte-Qwen2-7B-instruct` for embedding generation, where we set each worker to work from `START_IDX` to `END_IDX`. 

<details>
<summary>SkyPilot YAML for embedding generation</summary>

```yaml
name: compute-legal-embeddings

resources:
  accelerators: {L4:1, A100:1} 

envs:
  START_IDX: 0  # Will be overridden by batch_compute_vectors.py
  END_IDX: 10000  # Will be overridden by batch_compute_vectors.py
  MODEL_NAME: "Alibaba-NLP/gte-Qwen2-7B-instruct"
  EMBEDDINGS_BUCKET_NAME: sky-rag-embeddings  # Bucket name for storing embeddings


file_mounts:
  /output:
    name: ${EMBEDDINGS_BUCKET_NAME}
    mode: MOUNT

setup: |
  pip install torch==2.5.1 vllm==0.6.6.post1
  ...

envs: 
  MODEL_NAME: "Alibaba-NLP/gte-Qwen2-7B-instruct"

run: |
  python -m vllm.entrypoints.openai.api_server \
    --host 0.0.0.0 \
    --model $MODEL_NAME \
    --max-model-len 3072 \
    --task embed &

  python scripts/compute_embeddings.py \
    --start-idx $START_IDX \
    --end-idx $END_IDX \
    --chunk-size 2048 \
    --chunk-overlap 512 \
    --vllm-endpoint http://localhost:8000 
```

</details>

This automatically launches 10 SkyPilot managed jobs on L4 GPUs to processe documents from the Pile of Law dataset and computes embeddings in batches:
```
Processing documents: 100%|██████████| 1000/1000 [00:45<00:00, 22.05it/s]
Saving embeddings to: embeddings_0_1000.parquet
...
```
We leverage SkyPilot's managed jobs feature to enable parallel processing across multiple regions and cloud providers. 
SkyPilot handles job state management and automatic recovery from failures when using spot instances. 
Managed jobs are cost-efficient and streamline the processing of the partitioned dataset. 
You can check all the jobs by running `sky dashboard`.
<p align="center">
<img src="https://i.imgur.com/EviKuQx.png" alt="job dashboard" style="width: 70%;">
</p>
All generated embeddings are stored efficiently in parquet format within a cloud storage bucket.

## Step 2: Build RAG with Vector Database
After computing embeddings, construct a ChromaDB vector database for efficient similarity search:

```bash
sky launch build_rag.yaml --env EMBEDDINGS_BUCKET_NAME=$EMBEDDINGS_BUCKET_NAME --env VECTORDB_BUCKET_NAME=$VECTORDB_BUCKET_NAME
```

The process builds the database in batches:
```
Loading embeddings from: embeddings_0_1000.parquet
Adding vectors to ChromaDB: 100%|██████████| 1000/1000 [00:12<00:00, 81.97it/s]
...
```

## Step 3: Serve the RAG
Deploy the RAG service to handle queries and generate answers:

```bash
sky launch -c legal-rag serve_rag.yaml --env VECTORDB_BUCKET_NAME=$VECTORDB_BUCKET_NAME
```

Or use Sky Serve for managed deployment:
```bash
sky serve up -n legal-rag serve_rag.yaml --env VECTORDB_BUCKET_NAME=$VECTORDB_BUCKET_NAME
```

To query the system, get the endpoint:
```bash
sky serve status legal-rag --endpoint
```

You can visit the website and input your query there! A few queries to try out: 

> I want to break my lease. my landlord doesn't allow me to do that. 
> My employer has not provided the final paycheck after termination. 

## Disclaimer
This document provides instruction for building a RAG system with SkyPilot. The system and its outputs should not be considered as legal advice. Please consult qualified legal professionals for any legal matters.
