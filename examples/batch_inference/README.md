# Building A Billion Scale Image Vector Database With SkyPilot 

### Context: Semantic Search at Billion Scale 
Retrieval-Augmented Generation (RAG) is an advanced AI technique that enhances large language models (LLMs) by integrating external data sources into their responses.

### Step 0: Set Up The Environment
Setup Huggingface token in `~/.env`
```
HF_TOKEN=hf_xxxxx
```

To run the experiments 
```
python3 launch_clip.py
```

To construct the database from embeddings: 
```
sky jobs launch build_vectordb.yaml 
```

To query the constructed database: 
```
sky launch -c vecdb_serve serve_vectordb.yaml
```

```
ENDPOINT=$(sky status --ip vecdb_serve)
curl POST http://$ENDPOINT:8000/search \
  -H "Content-Type: application/json" \
  -d '{"text": "a photo of cloud", "n_results": 5}' | jq .
```