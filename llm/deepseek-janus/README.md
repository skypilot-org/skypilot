# Run Janus by DeepSeek with SkyPilot

> SkyPilot is a framework for running AI and batch workloads on any infra, offering unified execution, high cost savings, and high GPU availability.
<p align="center">
<img src="https://i.imgur.com/BGws6TV.png" alt="DeepSeek-Janus on SkyPilot" style="width: 70%;">
</p>

On Jan 27, 2025, DeepSeek AI released the [Janus](https://github.com/deepseek-ai/Janus). It outperforms **state-of-the-art Vision Language Models** such as LLaVA, supporting a variety of Vision-Language tasks such as image generation and Q&A.


This guide walks through how to run and host models **on any infrastructure** from ranging from Local GPU workstation, Kubernetes cluster and public clouds ([15+ clouds supported](https://docs.skypilot.co/en/latest/getting-started/installation.html)).

## Step 0: Bring any infra

Install SkyPilot on your local machine:

```bash
pip install 'skypilot-nightly[all]'
```

Pick one of the following depending on what infra you want to run:

**If your local machine/cluster has GPU**: you can run SkyPilot [directly on existing machines](https://docs.skypilot.co/en/latest/reservations/existing-machines.html).

**If you want to use Clouds** (15+ clouds are supported):
See [docs](https://docs.skypilot.co/en/latest/getting-started/installation.html) for details.


## Step 1: Run it with SkyPilot

Now it's time to run Janus with SkyPilot. Commands may vary based on the GPUs available to you.


Run Janus (1.5B) with:
```
sky launch janus_1.5b.yaml \
  -c janus \
  --secret HF_TOKEN=YOUR_HUGGING_FACE_API_TOKEN
```

or run Janus Pro (7B) with:

```
sky launch januspro_7b.yaml \
  -c janus \
  --secret HF_TOKEN=YOUR_HUGGING_FACE_API_TOKEN
```

It will show all the available machines that you have and pricing
```
...

Considered resources (1 node):
-----------------------------------------------------------------------------------------------------------------
 CLOUD        INSTANCE                   vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE        COST ($)   CHOSEN
-----------------------------------------------------------------------------------------------------------------
 Kubernetes   4CPU--16GB--1L4             4       16        L4:1           gke-cluster        0.00          ✔
 RunPod       1x_L4_SECURE               4       24        L4:1           CA                 0.44
 GCP          g2-standard-4              4       16        L4:1           us-east4-a         0.70
 AWS          g6.xlarge                  4       16        L4:1           us-east-1          0.80
 AWS          g5.xlarge                  4       16        A10G:1         us-east-1          1.01
 Fluidstack   A100_PCIE_80GB::1          28      120       A100-80GB:1    ARIZONA_USA        1.80
 RunPod       1x_A100-80GB_SECURE        8       80        A100-80GB:1    CA                 1.99
 Paperspace   A100-80G                   12      80        A100-80GB:1    East Coast (NY2)   3.18
 Azure        Standard_NV36ads_A10_v5    36      440       A10:1          eastus             3.20
 Azure        Standard_NC24ads_A100_v4   24      220       A100-80GB:1    eastus             3.67
 GCP          a2-ultragpu-1g             12      170       A100-80GB:1    us-central1-a      5.03
 Azure        Standard_ND96asr_v4        96      900       A100:8         eastus             27.20
 GCP          a2-highgpu-8g              96      680       A100:8         us-central1-a      29.39
 AWS          p4d.24xlarge               96      1152      A100:8         us-east-1          32.77
-----------------------------------------------------------------------------------------------------------------
````

## Step 2: Access the deployed server
You should be able to access directly via a terminal prompt via
```
Running on public URL: https://xxxxxx.gradio.live
```
or you can access through getting the IP address of the deployed instance via
```
echo `sky status --ip janus`
```

## Example Prompts
```
A blue sky,  vast fluffy clouds surrounding, enchanting, immortal, dynamic motion, cinematic, Unreal Engine 5 and Octane Render, highly detailed, photorealistic, natural colors, epic atmosphere, and breathtaking realism
```
<p align="center">
<img src="https://i.imgur.com/k4yh5BR.png" alt="DeepSeek-Janus on SkyPilot" style="width: 70%;">
</p>
