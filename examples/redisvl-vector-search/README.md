# RedisVL + SkyPilot: Vector Search at Scale

Distributed vector search over [1M research papers](https://www.kaggle.com/datasets/nechbamohammed/research-papers-dataset) using [RedisVL](https://docs.redisvl.com/en/latest/) and [SkyPilot](https://skypilot.readthedocs.io/en/latest/).

📖 [Read the full blog post](https://blog.skypilot.co/redisvl-skypilot/).


## Features

- Distributed GPU-accelerated embedding generation
- Vector search with RedisVL over 1M research papers
- Automatic failover and retry with SkyPilot managed jobs
- Direct streaming to Redis (no intermediate storage)
- Cost-effective: ~$0.85 to embed entire 1M paper dataset (5 parallel T4 spot instances on GCP @ 80 mins)

## Setup

```bash
pip install -r requirements.txt
```

Create `.env` file:
```bash
REDIS_HOST=your-redis-host.redislabs.com
REDIS_PORT=12345
REDIS_USER=default
REDIS_PASSWORD=your-password
```

## Launch Jobs

Batch launcher (recommended):
```bash
python batch_embedding_launcher.py --num-jobs 5 --env-file .env
```

Single managed job:
```bash
sky jobs launch embedding_job.yaml \
  --env JOB_START_IDX=0 \
  --env JOB_END_IDX=200000 \
  --env-file .env 
```

Monitor:
```bash
sky jobs queue
sky dashboard
```

## Run Search API

### Option 1: Deploy with SkyPilot

Deploy the search API and Streamlit UI to the cloud:

```bash
sky launch -c redisvl-search-api search_api.yaml --env-file .env
```

Access the services:

- FastAPI:
```
export API_ENDPOINT=$(sky status --endpoint 8001 redisvl-search-api)
echo $API_ENDPOINT

curl -X POST "http://$API_ENDPOINT/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "neural networks", "k": 5}'
```

API in action:

<div align="center">
  <video src="assets/api_service.mp4" width="700" controls autoplay muted></video>
</div>

- Streamlit UI:
```
export STREAMLIT_ENDPOINT=$(sky status --endpoint 8501 redisvl-search-api)
echo $STREAMLIT_ENDPOINT
```

Streamlit app interface:

<div align="center">
  <video src="assets/streamlit_app.mp4" width="700" controls autoplay muted></video>
</div>


Tear down when done:
```bash
sky down redisvl-search-api
```

### Option 2: Run Locally

```bash
source .env
python app.py
```

## Test

```bash
curl -X POST "http://localhost:8000/search" \
  -H "Content-Type: application/json" \
  -d '{"query": "neural networks", "k": 5}'
```

## Files

- `app.py` - FastAPI search service
- `batch_embedding_launcher.py` - Job launcher
- `compute_embeddings.py` - Embedding generation
- `embedding_job.yaml` - SkyPilot job config
- `streamlit_app.py` - Search UI