# Retrieval Augmented Generation with DeepSeek R1

<p align="center">
<img src="https://i.imgur.com/fI6Mzpi.png" alt="RAG with DeepSeek R1" style="width: 70%;">
</p>

## Large-Scale Legal Document Search and Analysis
As legal document collections grow, traditional keyword search becomes insufficient for finding relevant information. Retrieval-Augmented Generation (RAG) combines the power of vector search with large language models to enable semantic search and intelligent answers.

In particular:

* **Accuracy**: RAG systems ground their responses in source documents, reducing hallucination and improving answer reliability.
* **Context-Awareness**: By retrieving relevant documents before generating answers, the system provides responses that consider specific legal contexts.
* **Traceability**: All generated answers can be traced back to source documents, crucial for legal applications.

SkyPilot streamlines the deployment of RAG systems in the cloud by managing infrastructure and enabling efficient, cost-effective compute resource usage. We use [Alibaba-NLP/gte-Qwen2-7B-instruct] for generating document embeddings and distilled Deepseek R1 ([deepseek-ai/DeepSeek-R1-Distill-Llama-8B]) for generating final anwsers. 

In this example, we use legal documents by [pile of law](https://huggingface.co/datasets/pile-of-law/pile-of-law) as example data to demonstrate RAG capabilities. The system processes a collection of legal texts, including case law, statutes, and legal discussions, to enable semantic search and intelligent question answering. This approach can help legal professionals quickly find relevant precedents, analyze complex legal scenarios, and extract insights from large document collections. 

## Step 0: Set Up The Environment
Install the following Prerequisites:  
* SkyPilot: Ensure SkyPilot is installed and `sky check` succeeds. See [installation instructions](https://docs.skypilot.co/en/latest/getting-started/installation.html).
* Hugging Face Token: Required to access the DeepSeek R1 model. Configure your token:

Setup Huggingface token in `~/.env`
```
HF_TOKEN=hf_xxxxx
```
or set as environment variable `HF_TOKEN`.

## Step 1: Compute Embeddings from Legal Documents
Convert legal documents into vector representations using DeepSeek R1. These embeddings enable semantic search across the document collection.

Launch the embedding computation:
```bash
python3 batch_compute_embeddings.py
```

This processes documents from the Pile of Law dataset and computes embeddings in batches:
```
Processing documents: 100%|██████████| 1000/1000 [00:45<00:00, 22.05it/s]
Saving embeddings to: embeddings_0_1000.parquet
...
```

## Step 2: Build RAG with Vector Database
After computing embeddings, construct a ChromaDB vector database for efficient similarity search:

```bash
sky jobs launch build_rag.yaml
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
sky launch -c rag_serve serve_rag.yaml
```

Or use Sky Serve for managed deployment:
```bash
sky serve up serve_rag.yaml -n legal-rag
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