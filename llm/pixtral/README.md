# Deploy Pixtral Privately On Your Kubernetes or Your Own Cloud



```
                                                                 ▄▄▄░░
                                                        ▄▄▄▄▄█████████░░░░
                                            ▄▄▄▄▄▄████████████████████░░░░░
                                         █████████████████████████████░░░░░
                    ▄▄▄▄▄▄█████░░░       █████████████████████████████░░░░░
         ▄▄▄▄▄██████████████████░░░░░░  ██████████████████████████████░░░░░
  ▄█████████████████████████████░░░░░░░░██████████████████████████████░░░░░
  ███████████████████████████████░░░░░░░██████████████████████████████░░░░░
  ███████████████████████████████░░░░░░░██████████████████████████████░░░░░
  ███████████████████████████████░░░░░░███████████████████████████████░░░░░
  ████████████████████████████████░░░░░███████████████████████████████░░░░░
  ████████████████████████████████░░░░████████████████████████████████░░░░░
  █████████████████████████████████░░░████████████████████████████████░░░░░
  █████████████████████████████████░░░████████████░███████████████████░░░░░
  ██████████████████████████████████░█████████████░███████████████████░░░░░
  ███████████████████░██████████████▄█████████████░███████████████████░░░░░
  ███████████████████░███████████████████████████░░███████████████████░░░░░
  ███████████████████░░██████████████████████████░░███████████████████░░░░░
  ███████████████████░░█████████████████████████░░░███████████████████░░░░░
  ███████████████████░░░████████████████████████░░░███████████████████░░░░░
  ███████████████████░░░████████████████████████░░░███████████████████░░░░░
  ███████████████████░░░░██████████████████████░░░░███████████████████░░░░░
  ███████████████████░░░░██████████████████████░░░░███████████████████░░░░░
  ███████████████████░░░░░█████████████████████░░░░███████████████████░░░░░
  ███████████████████░░░░░████████████████████░░░░░███████████████████░░░░░
  ███████████████████░░░░░░███████████████████░░░░░███████████████████░░░░░
  ███████████████████░░░░░░██████████████████░░░░░░███████████████████░░░░░
  ███████████████████░░░░░░░█████████████████░░░░░░███████████████████░░░░░
  ███████████████████░░░░░░░█████████████████░░░░░░███████████████████░░░░░
  ███████████████████░░░░░░░░███████████████░░░░░░░██████████░░░░░░░░░░░░░░
  ███████████████████░░░░░░░░███████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
  ███████████████████░░░░░░░░███████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
  ███████████████████░░░░░░░░░██░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
  ███████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
  ██████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  ░░░░░░░
      ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░      ░░░
            ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    ░░░░░░░░░░░░░░░░░░
               ░░░░░░░░░░░░░░░░░░░░░░░░░░░░
                    ░░░░░░░░░░░░░░░░░
                       ░░░░░
```

On Sep 11, 2024, Mistral released a new [Pixtral 12B](https://x.com/MistralAI/status/1833758285167722836), their first multimodal model, supporting both text and image inputs.

This guide shows how to use run and deploy this multimodal model on your own clouds or Kubernetes clusters.

## Run Pixtral on Any Cloud or Kubernetes

1. Install SkyPilot on your local machine and check your kubernetes and cloud setup:

```bash
pip install 'skypilot[all]'
sky check
```
Detailed instructions for installation and cloud setup [here](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html).

2. Launch the model on any cloud or Kubernetes:
```bash
sky launch -c pixtral pixtral.yaml
```

<center>
  <img src="https://i.imgur.com/afzNiaK.png" alt="Pixtral on SkyPilot" width="80%" style="display:block; margin:auto;">
</center>


3. Get the endpoint and send requests:
```bash
ENDPOINT=$(sky status --endpoint 8081 pixtral)

curl http://$ENDPOINT/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer token' \
    --data '{
        "model": "mistralai/Pixtral-12B-2409",
        "messages": [
        {
            "role": "user",
            "content": [
                {"type" : "text", "text": "Describe this image in detail please."},
                {"type": "image_url", "image_url": {"url": "https://s3.amazonaws.com/cms.ipressroom.com/338/files/201808/5b894ee1a138352221103195_A680%7Ejogging-edit/A680%7Ejogging-edit_hero.jpg"}},
                {"type" : "text", "text": "and this one as well."},
                {"type": "image_url", "image_url": {"url": "https://www.wolframcloud.com/obj/resourcesystem/images/a0e/a0ee3983-46c6-4c92-b85d-059044639928/6af8cfb971db031b.png"}}
            ]
        }],
        "max_tokens": 1024
    }' | jq .
```

In this example, we send two images to the model and ask it to describe them.


<center>
  <img src="https://s3.amazonaws.com/cms.ipressroom.com/338/files/201808/5b894ee1a138352221103195_A680%7Ejogging-edit/A680%7Ejogging-edit_hero.jpg" alt="Image1" width="300" style="display:inline-block; margin-right:10px;">
  <img src="https://www.wolframcloud.com/obj/resourcesystem/images/a0e/a0ee3983-46c6-4c92-b85d-059044639928/6af8cfb971db031b.png" alt="Image2" width="300" style="display:inline-block;">
</center>


4. Example output (parsed version):
```markdown
Sure! Let me describe the images for you.

### Image 1:
This image shows three people jogging outdoors in a lush, green setting. The person on the left is a man wearing a light gray T-shirt and black shorts. He appears to be smiling and is actively running. The person in the middle is a woman with curly hair, dressed in a bright yellow tank top and black shorts. She also looks happy and is running alongside the man. The person on the right is another woman with long, wavy hair, wearing a light pink T-shirt and dark leggings. She is smiling and running as well. The background is filled with dense greenery, suggesting they are in a park or a forest.

### Image 2:
This image features a family of five posing together in a studio setting. The family members are all dressed in matching red outfits. From left to right, the first person is a woman with long blonde hair. Next to her is a young boy with light brown hair. The third person is a man with short dark hair and a mustache, smiling broadly. The fourth person is another young boy with dark hair and a slight smile. The last person is another woman with long blonde hair, mirroring the first woman. They are all laying on the floor, facing forward, with their hands clasped together in front of them. The background is plain white, focusing the attention on the family.

These descriptions should give you a clear picture of the scenes depicted in the images.
```

<details>
<summary>Raw JSON</summary>

```json
{
  "id": "chat-5733a2abfd664a019c7c61e38bb6603c",
  "object": "chat.completion",
  "created": 1726103777,
  "model": "mistralai/Pixtral-12B-2409",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Sure! Let me describe the images for you.\n\n### Image 1:\nThis image shows three people jogging outdoors in a lush, green setting. The person on the left is a man wearing a light gray T-shirt and black shorts. He appears to be smiling and is actively running. The person in the middle is a woman with curly hair, dressed in a bright yellow tank top and black shorts. She also looks happy and is running alongside the man. The person on the right is another woman with long, wavy hair, wearing a light pink T-shirt and dark leggings. She is smiling and running as well. The background is filled with dense greenery, suggesting they are in a park or a forest.\n\n### Image 2:\nThis image features a family of five posing together in a studio setting. The family members are all dressed in matching red outfits. From left to right, the first person is a woman with long blonde hair. Next to her is a young boy with light brown hair. The third person is a man with short dark hair and a mustache, smiling broadly. The fourth person is another young boy with dark hair and a slight smile. The last person is another woman with long blonde hair, mirroring the first woman. They are all laying on the floor, facing forward, with their hands clasped together in front of them. The background is plain white, focusing the attention on the family.\n\nThese descriptions should give you a clear picture of the scenes depicted in the images.",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": null
    }
  ],
  "usage": {
    "prompt_tokens": 4457,
    "total_tokens": 4764,
    "completion_tokens": 307
  },
  "prompt_logprobs": null
}
```

</details>


## Scale Up Pixtral Endpoint as a Service

1. Start a service with [SkyServe](https://skypilot.readthedocs.io/en/latest/serving/sky-serve.html):
```bash
sky serve up -n pixtral pixtral.yaml
```

2. Check the status of the services:

```bash
sky serve status pixtral
```

![Serve Pixtral](https://i.imgur.com/WHGZICc.png)

3. Get the endpoint and send requests:
```bash
ENDPOINT=$(sky serve status --endpoint pixtral)

curl http://$ENDPOINT/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer token' \
    --data '{
        "model": "mistralai/Pixtral-12B-2409",
        "messages": [
        {
            "role": "user",
            "content": [
                {"type" : "text", "text": "Turn this logo into ASCII art."},
                {"type": "image_url", "image_url": {"url": "https://pbs.twimg.com/profile_images/1584596138635632640/HWexMoH5_400x400.jpg"}}
            ]
        }],
        "max_tokens": 1024
    }' | jq .
```


4. Example output (parsed version):
````markdown
Here's the logo of SkyPilot converted into ASCII art:
```
      ______
  ___//  __\\____
   / __ \  __/  __\\
   |_\ \_\\___ ____ ___\\
    \/    \/   \/   \/
````

<details>
<summary>Raw JSON</summary>

```json
{
  "id": "chat-414fb85491ec42809f54a83845fdd629",
  "object": "chat.completion",
  "created": 1726109048,
  "model": "mistralai/Pixtral-12B-2409",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Here's a simple ASCII representation of the Android logo:\n\n```\n      ______\n  ___//  __\\\\____\n / __ \\  __/  __\\\\\n|_\\ \\_\\\\___ ____ ___\\\\\n \\/    \\/   \\/   \\/\n```",
        "tool_calls": []
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": null
    }
  ],
  "usage": {
    "prompt_tokens": 660,
    "total_tokens": 716,
    "completion_tokens": 56
  },
  "prompt_logprobs": null
}
```
