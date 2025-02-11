# Legal RAG with DeepSeek R1

This example demonstrates how to build a Retrieval-Augmented Generation (RAG) system using the [Pile of Law dataset](https://huggingface.co/datasets/pile-of-law/pile-of-law) and [DeepSeek R1](https://github.com/deepseek-ai/DeepSeek-R1) model. The system uses DeepSeek R1 for both computing embeddings and generating answers.

## System Overview

The RAG system consists of three main components:

1. **Embedding Computation** (`compute_vectors.py`):
   - Loads documents from the Pile of Law dataset
   - Chunks documents into smaller pieces
   - Uses DeepSeek R1 through vLLM to compute embeddings
   - Saves embeddings to parquet files

2. **Vector Database Building** (`build_vectordb.py`):
   - Loads computed embeddings
   - Builds a ChromaDB vector database
   - Saves the database for fast similarity search

3. **RAG Service** (`serve_rag.py`):
   - Serves a FastAPI application
   - Uses DeepSeek R1 for both retrieval and answer generation
   - Provides a simple web interface for querying

## Requirements

- SkyPilot installed and configured
- Access to GPU instances (T4, L4, A10G, A10, or V100)
- HuggingFace account for accessing DeepSeek R1 model

## Usage

### 1. Compute Embeddings

This step processes the first 100k documents from Pile of Law and computes their embeddings:

```bash
sky launch compute_embeddings.yaml
```

The script will:
- Start a vLLM service with DeepSeek R1
- Process documents in chunks of 1000
- Save embeddings to parquet files

### 2. Build Vector Database

After computing embeddings, build the vector database:

```bash
sky launch build_vectordb.yaml
```

This will:
- Load all computed embeddings
- Build a ChromaDB database
- Save it for fast similarity search

### 3. Serve RAG System

Finally, launch the RAG service:

```bash
sky launch serve_rag.yaml
```

The service will:
- Start vLLM with DeepSeek R1
- Load the vector database
- Serve the RAG system on port 8001

### Query the System

You can query the system using curl:

```bash
curl -X POST http://localhost:8001/rag \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What are the key elements of a contract?",
    "n_results": 3,
    "temperature": 0.7
  }'
```

The response will include:
- Generated answer from DeepSeek R1
- Source documents used
- Similarity scores
- Document metadata (source, split, etc.)

## Implementation Details

### Embedding Computation

- Uses DeepSeek R1 through vLLM's embedding endpoint
- Chunks documents with overlap for better context
- Processes in batches for efficiency
- Includes retry logic for robustness

### Vector Search

- Uses ChromaDB for fast similarity search
- Stores document metadata for traceability
- Converts distances to similarity scores

### Answer Generation

- Uses DeepSeek R1 through vLLM
- Includes relevant context from retrieved documents
- Maintains source attribution

## Configuration

You can adjust various parameters in the YAML files:

- `compute_embeddings.yaml`:
  - Chunk size and overlap
  - Batch size for embedding computation
  - Number of documents to process

- `build_vectordb.yaml`:
  - Memory allocation
  - Batch size for database building

- `serve_rag.yaml`:
  - GPU selection
  - Model parameters
  - Service ports

## Performance Considerations

- Uses spot instances for cost savings
- Supports multiple GPU types for flexibility
- Processes documents in chunks to manage memory
- Uses batching for efficient GPU utilization

## Limitations

- Currently processes first 100k documents
- Requires GPU for both embedding and serving
- Limited by vLLM's embedding API capabilities
- May need tuning for very large document collections
