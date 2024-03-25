# TGI: Hugging Face Text Generation Inference


Text Generation Inference (TGI) is a toolkit for deploying and serving Large Language Models (LLMs) for text generation tasks from Hugging Face.


## Launch a Single-Instance TGI Serving

We can host the model with a single instance using service [YAML](https://github.com/skypilot-org/skypilot/blob/master/llm/tgi/serve.yaml):

```bash
sky launch -c tgi serve.yaml
```

A user can access the model with the following command:

```bash
ENDPOINT=$(sky status --endpoint 8080 tgi)

curl -L $(sky serve status tgi --endpoint)/generate \
    -H 'Content-Type: application/json' \
    -d '{
      "inputs": "What is Deep Learning?",
      "parameters": {
        "max_new_tokens": 20
      }
    }'
```

The output should be similar to the following:

```console
{
  "generated_text": "What is Deep Learning? Deep Learning is a subfield of machine learning that is concerned with algorithms inspired by the structure and function of the brain called artificial neural networks."
}
```




## Scale the Serving with SkyPilot Serve

Using the same YAML, we can easily scale the model serving across multiple instances, regions and clouds with SkyServe:

```bash
sky serve up -n tgi serve.yaml
```

After the service is launched, we can access the model with the following command:

```bash
ENDPOINT=$(sky serve status --endpoint tgi)

curl -L $ENDPOINT/generate \
    -H 'Content-Type: application/json' \
    -d '{
      "inputs": "What is Deep Learning?",
      "parameters": {
        "max_new_tokens": 20
      }
    }'
```





