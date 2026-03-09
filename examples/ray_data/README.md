# Synthetic Data Generation using Ray Data

## What is Ray Data?

Ray Data is a distributed data processing library designed for AI and machine learning pipelines, built on top of Ray. It offers high-level, efficient APIs for tasks like batch inference, dataset preprocessing, and feeding data into ML training workflows. Unlike many traditional distributed data frameworks, Ray Data uses a streaming execution model that processes data incrementally, allowing it to handle very large datasets while keeping both CPU and GPU resources consistently utilized. This design helps maintain high throughput and scalability across diverse AI workloads.

## SkyPilot Installation

Install SkyPilot:

```bash
uv venv --seed --python 3.10
source .venv/bin/activate
uv pip install "skypilot[gcp,kubernetes]"
```

This example demonstrates the use of both GCP and Kubernetes as infrastructure backends, selecting one of them at random during execution. You can adjust the configuration to match your preferred cloud environment by adding or removing backends as needed. To set up the required dependencies and configuration, follow the installation steps provided in the official [SkyPilot documentation](https://docs.skypilot.co/en/latest/getting-started/installation.html)

## Running Ray Data Workloads

In this example, we will use Ray Data to generate synthetic QA data. Synthetic data generation has become increasingly popular, with organizations producing trillions of tokens to support large-scale language model pretraining.

For this demonstration, we will load 1,000 rows from the wikitext dataset and generate multiple structured question–answer pairs for each text passage. To achieve high throughput, we will use Ray Data LLM, which relies on vLLM for efficient model serving.

This example is inspired by Ray Data’s [Batch Inference with Structural Outputs (Guided Decoding)](https://docs.ray.io/en/latest/llm/examples/batch/vllm-with-structural-output.html) tutorial.


In this example, either Kubernetes or GCP is randomly selected to provision two A100 machines for the task. You can modify the resources section to use the infrastructure provider of your choice.

During the setup phase, the required NVIDIA packages and Ray Data dependencies are installed. In the run phase, a Python script is executed to generate the synthetic data and write the results to the attached storage volume.

To run the example simply run -

```
sky launch launcher.yaml
```