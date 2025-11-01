//! RAG (Retrieval-Augmented Generation) system

use anyhow::Result;
use serde::{Deserialize, Serialize};
use styx_sky::{launch, Resources, Task};

/// RAG configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RAGConfig {
    pub llm_model: String,
    pub embedding_model: String,
    pub vector_db: VectorDB,
    pub chunk_size: usize,
    pub chunk_overlap: usize,
    pub top_k: usize,
}

impl Default for RAGConfig {
    fn default() -> Self {
        Self {
            llm_model: "meta-llama/Meta-Llama-3-8B-Instruct".to_string(),
            embedding_model: "BAAI/bge-large-en-v1.5".to_string(),
            vector_db: VectorDB::ChromaDB,
            chunk_size: 1000,
            chunk_overlap: 200,
            top_k: 5,
        }
    }
}

/// Vector database options
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum VectorDB {
    ChromaDB,
    Pinecone,
    Weaviate,
    Qdrant,
    Milvus,
}

/// RAG system
pub struct RAGSystem {
    config: RAGConfig,
    task_id: Option<String>,
}

impl RAGSystem {
    pub fn new(config: RAGConfig) -> Self {
        Self {
            config,
            task_id: None,
        }
    }

    /// Deploy RAG system
    pub async fn deploy(&mut self) -> Result<String> {
        let task = Task::new()
            .with_name("rag-system")
            .with_setup(&self.generate_setup())
            .with_run(&self.generate_run())
            .with_resources(Resources::new().with_accelerator("A100", 2));

        let task_id = launch(task, None, false).await?;
        self.task_id = Some(task_id.clone());
        Ok(task_id)
    }

    fn generate_setup(&self) -> String {
        let vector_db_setup = match self.config.vector_db {
            VectorDB::ChromaDB => "pip install chromadb",
            VectorDB::Pinecone => "pip install pinecone-client",
            VectorDB::Weaviate => "pip install weaviate-client",
            VectorDB::Qdrant => "pip install qdrant-client",
            VectorDB::Milvus => "pip install pymilvus",
        };

        format!(
            r#"
# Create environment
conda activate rag || conda create -n rag python=3.10 -y
conda activate rag

# Install dependencies
pip install torch transformers sentence-transformers
pip install langchain langchain-community
pip install {}
pip install fastapi uvicorn
pip install pypdf python-docx
"#,
            vector_db_setup
        )
    }

    fn generate_run(&self) -> String {
        format!(
            r#"
python -c "
import os
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.vectorstores import Chroma
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.llms import HuggingFacePipeline
from langchain.chains import RetrievalQA
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import torch

# Initialize embeddings
embeddings = HuggingFaceEmbeddings(
    model_name='{}',
    model_kwargs={{'device': 'cuda'}}
)

# Initialize vector store
vectorstore = Chroma(
    collection_name='documents',
    embedding_function=embeddings,
    persist_directory='./chroma_db'
)

# Load LLM
tokenizer = AutoTokenizer.from_pretrained('{}')
model = AutoModelForCausalLM.from_pretrained(
    '{}',
    torch_dtype=torch.float16,
    device_map='auto'
)

pipe = pipeline(
    'text-generation',
    model=model,
    tokenizer=tokenizer,
    max_new_tokens=512,
    temperature=0.7,
)

llm = HuggingFacePipeline(pipeline=pipe)

# Create RAG chain
qa_chain = RetrievalQA.from_chain_type(
    llm=llm,
    chain_type='stuff',
    retriever=vectorstore.as_retriever(search_kwargs={{'k': {}}}),
    return_source_documents=True,
)

# Start API server
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI(title='RAG API')

class Query(BaseModel):
    question: str

class Response(BaseModel):
    answer: str
    sources: list

@app.post('/query', response_model=Response)
async def query(q: Query):
    result = qa_chain(q.question)
    return Response(
        answer=result['result'],
        sources=[doc.page_content[:200] for doc in result['source_documents']]
    )

@app.post('/ingest')
async def ingest_documents(texts: list[str]):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size={},
        chunk_overlap={},
    )
    splits = text_splitter.split_documents(texts)
    vectorstore.add_documents(splits)
    return {{'status': 'ok', 'chunks': len(splits)}}

uvicorn.run(app, host='0.0.0.0', port=8000)
"
"#,
            self.config.embedding_model,
            self.config.llm_model,
            self.config.llm_model,
            self.config.top_k,
            self.config.chunk_size,
            self.config.chunk_overlap,
        )
    }
}

/// LocalGPT - privacy-focused RAG
pub struct LocalGPT {
    model: String,
}

impl LocalGPT {
    pub fn new(model: impl Into<String>) -> Self {
        Self {
            model: model.into(),
        }
    }

    pub async fn deploy(&self) -> Result<String> {
        let task = Task::new()
            .with_name("localgpt")
            .with_setup(
                r#"
# Clone LocalGPT
git clone https://github.com/PromtEngineer/localGPT
cd localGPT

# Create environment
conda create -n localgpt python=3.10 -y
conda activate localgpt

# Install dependencies
pip install -r requirements.txt
pip install llama-cpp-python
"#,
            )
            .with_run(&format!(
                r#"
cd localGPT

# Ingest documents
python ingest.py

# Run server
python run_localGPT.py --model {}
"#,
                self.model
            ))
            .with_resources(Resources::new().with_accelerator("A100", 1));

        launch(task, None, false).await
    }
}
