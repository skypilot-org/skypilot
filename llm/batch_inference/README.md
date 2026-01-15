# Large-Scale AI Batch Inference: 9x Faster Embedding Generation

<p align="center">
<img src="https://i.imgur.com/mGugNob.png" alt="Large-Scale Embedding Generation with SkyPilot" style="width: 70%;">
</p>

## Large-Scale Embedding Generation for Text
As the volume of text data grows, the need for efficient and powerful embedding generation methods becomes critical. Embedding generation is at the heart of modern AI applications, from recommendation systems to retrieval augmented generation (RAG).

However, running batch inference for embedding generation on million/trillion-scale datasets is not trivial. After spending days developing a well-tuned batch inference script, **getting hundreds of GPUs and running jobs on those GPUs in parallel are still a huge pain**.

In particular:

* **Scalability**: Modern apps can deal with millions or billions of text records, making traditional approaches slow or impractical.
* **Availability**: GPU quota and availability constraints in a single region severely limit processing capacity.
* **Cost**: On-demand GPU instances for large-scale processing can be prohibitively expensive.

SkyPilot streamlines the process of running such large-scale jobs in the cloud. It abstracts away much of the complexity of managing infrastructure and helps you run compute-intensive tasks efficiently and cost-effectively through managed jobs across multiple regions.

## Performance Highlights

By leveraging SkyPilot's multi-region approach, we achieved:

- **9x More Resources**: Access to 406 GPUs across 12 regions (vs. only ~47 in a single region)
- **10x Faster Processing**: Reduced processing time from 20+ hours to just 2 hours
- **61% Cost Reduction**: Lowered costs from $710 to $277.07 through spot instance usage
- **Enhanced Reliability**: Automatic recovery from spot instance preemptions

## Compute Embeddings from Text Data with LLM Models

You need to convert text into vector representations (embeddings) so they can be stored in a vector database. 

We use the book partition of the [Amazon reviews 2023](https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/tree/main), containing ~30M Amazon reviews for books, and generating the embeddings for the reviews with the state-of-the-art specialized embedding LLM `Alibaba-NLP/gte-Qwen2-7B-instruct`, one of the top embedding models on the [MTEB leaderboard](https://huggingface.co/spaces/mteb/leaderboard).


Use the following command to launch a job that processes your text dataset and computes embeddings: 
```
python3 batch_compute_vectors.py
```
This will automatically find available machines across multiple regions to compute the vectors. The script partitions the workload evenly using a stride approach, ensuring each worker processes documents that are distributed throughout the dataset.



## Monitor the progress

You can use `sky jobs queue` and `sky dashboard` to see the status of jobs. Alternatively, you can monitor the progress via 
```
sky launch -n monitor monitor_progress.yaml 
```
and get the IP address via 
```
export ENDPOINT=$(sky status --ip monitor)
```
and visit `http:$ENDPOINT:8000` in the browser. 
<p align="center">
<img src="https://i.imgur.com/8cMp3l1.png" alt="Large-Scale Embedding Generation with SkyPilot" style="width: 70%;">
</p>

## Learn More

For a complete walkthrough of this case study, including detailed performance metrics and implementation insights, read our [blog post on large-scale embedding generation](https://blog.skypilot.co/large-scale-embedding/).
