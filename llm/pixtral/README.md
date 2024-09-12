# Deploy Pixtral with One Click on Any Cloud or Kubernetes

On Sep 11, 2024, Mistral released a new [Pixtral 12B](https://x.com/MistralAI/status/1833758285167722836), their first multimodal model. This guide shows how to use run and deploy this multimodal model on any cloud and Kubernetes.


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

## Run Pixtral on Any Cloud or Kubernetes

1. Install SkyPilot on your local machine:

```bash
pip install 'skypilot-nightly[all]'
```

2. Launch the model on any cloud or Kubernetes:
```
sky launch -c pixtral pixtral.yaml
```

```
--------------------------------------------------------------------------------------------------------------
 CLOUD        INSTANCE                   vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE     COST ($)   CHOSEN   
--------------------------------------------------------------------------------------------------------------
 Kubernetes   4CPU--8GB--1L4             4       16        L4:1           kubernetes      0.00          ✔ 
 RunPod       1x_L4_SECURE               4       24        L4:1           CA              0.44               
 GCP          g2-standard-4              4       16        L4:1           us-east4-a      0.70                
 AWS          g6.xlarge                  4       16        L4:1           us-east-1       0.80                
 AWS          g5.xlarge                  4       16        A10G:1         us-east-1       1.01                
 RunPod       1x_L40_SECURE              16      48        L40:1          CA              1.14                
 Fluidstack   L40_48GB::1                32      60        L40:1          CANADA          1.15                
 AWS          g6e.xlarge                 4       32        L40S:1         us-east-1       1.86                
 RunPod       1x_A100-80GB_SECURE        8       80        A100-80GB:1    CA              1.99                
 Azure        Standard_NV36ads_A10_v5    36      440       A10:1          eastus          3.20                
 Azure        Standard_NC24ads_A100_v4   24      220       A100-80GB:1    eastus          3.67                
 GCP          a2-highgpu-1g              12      85        A100:1         us-central1-a   3.67                
 GCP          a2-ultragpu-1g             12      170       A100-80GB:1    us-central1-a   5.03                
--------------------------------------------------------------------------------------------------------------
```

3. Get the endpoint and send requests:
```
ENDPOINT=$(sky status --endpoint 8081 pixtral)

curl --location http://$ENDPOINT/v1/chat/completions \
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
                {"type": "image_url", "image_url": {"url": "https://www.wolframcloud.com/obj/resourcesystem/images/a0e/a0ee3983-46c6-4c92-b85d-059044639928/6af8cfb971
db031b.png"}}
            ]
        }],
        "max_tokens": 1024
    }' | jq .
```


## Scale Up Pixtral Endpoint as a Service

1. Start a service with SkyServe:
```
sky serve up -n pixtral pixtral.yaml
```

2. Get the endpoint and send requests:
```
ENDPOINT=$(sky serve status --endpoint pixtral)

curl --location http://$ENDPOINT/v1/chat/completions \
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
                {"type": "image_url", "image_url": {"url": "https://www.wolframcloud.com/obj/resourcesystem/images/a0e/a0ee3983-46c6-4c92-b85d-059044639928/6af8cfb971
db031b.png"}}
            ]
        }],
        "max_tokens": 1024
    }' | jq .
```
