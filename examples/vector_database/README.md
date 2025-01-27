# Building A Million Scale Image Vector Database With SkyPilot 

### Semantic Search at Million (Billion) Scale 
As the volume of image data grows, the need for efficient and powerful search methods becomes critical. Traditional keyword-based or metadata-based search often fails to capture the full semantic meaning in images. A vector database enables semantic search: you can find images that conceptually match a query (e.g., "a photo of a cloud") rather than relying on textual tags.

In particular:

* **Scalability**: Modern apps can deal with millions or billions of images, making typical database solutions slower or harder to manage.
* **Flexibility**: Storing image embeddings as vectors allows you to adapt to different search use-cases, from "find similar products" to "find images with specific objects or styles."
* **Performance**: Vector databases are optimized for nearest neighbor queries in high-dimensional spaces, enabling real-time or near real-time search on large datasets.

SkyPilot streamlines the process of running such large-scale jobs in the cloud. It abstracts away much of the complexity of managing infrastructure and helps you run compute-intensive tasks efficiently and cost-effectively through managed jobs. 

### Step 0: Set Up The Environment
Install the following Prerequisites:  
* SkyPilot: Make sure you have SkyPilot installed and configured. Refer to [SkyPilot’s documentation](https://docs.skypilot.co/en/latest/getting-started/installation.html) for instructions.
* Hugging Face Token: If you are using a private model or want faster/authorized downloads from the Hugging Face Hub, you will need your token. Follow the steps below to configure your token.

Setup Huggingface token in `~/.env`
```
HF_TOKEN=hf_xxxxx
```
or set up the environment variable `HF_TOKEN`. 

### Step 1: Compute Vectors from Image Data with CLIP
We need to convert images into vector representations (embeddings) so they can be stored in a vector database. Models like [CLIP by OpenAI](https://openai.com/index/clip/) learn powerful representations that map images and text into the same embedding space. This allows for semantic similarity calculations, making queries like “a photo of a cloud” match relevant images.

Use the following command to launch a job that processes your image dataset and computes the CLIP embeddings: 
```
python3 batch_compute_vectors.py
```
This will automatically find the cheapest available machines to compute the vectors. 


### Step 2: Construct the Vector Database from Computed Embeddings
Once you have the image embeddings, you need a specialized engine to perform rapid similarity searches at scale. This step ingests the embeddings from Step 1 into a vector database to enable real-time or near real-time search over millions of vectors. 

To construct the database from embeddings: 
```
sky jobs launch build_vectordb.yaml 
```

This process the generated clip embeddings in batches, generating output: 
```
(vectordb-build, pid=2457) INFO:__main__:Processing /clip_embeddings/embeddings_0_500.parquet_part_0/data.parquet
Processing batches: 100%|██████████| 1/1 [00:00<00:00,  1.19it/s]
Processing files:  92%|█████████▏| 11/12 [00:02<00:00,  5.36it/s]INFO:__main__:Processing /clip_embeddings/embeddings_500_1000.parquet_part_0/data.parquet
Processing batches: 100%|██████████| 1/1 [00:02<00:00,  2.39s/it]
Processing files: 100%|██████████| 12/12 [00:05<00:00,  2.04it/s]/1 [00:00<?, ?it/s]
```

### Step 3: Serve the Constructed Vector Database

A vector database is only useful if you can *query* it. By serving the database, you expose an API endpoint that other applications (or your local client) can call to perform semantic search.

To serve the constructed database: 
```
sky launch -c vecdb_serve serve_vectordb.yaml
```
you can also run 
```
sky serve up serve_vectordb.yaml -n vectordb
```
This will deploy your vector database as a service on a cloud instance and a llow you to interact with it via a public endpoint.


### Step 4: Query the Deployed Vector Database
Querying allows you to confirm that your database is working and retrieve semantic matches for a given text query. You can integrate this endpoint into larger applications (like an image search engine or recommendation system).

To query the constructed database, 

If you run through `sky launch`, use 
```
$(sky status --ip vecdb_serve)
```
deployed cluster. 

If you run through `sky serve`, you may run
```
sky serve status vectordb --endpoint
```

to get the endpoint address of the service. 