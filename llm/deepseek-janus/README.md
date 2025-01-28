# Run Janus by DeepSeek with SkyPilot

> SkyPilot is a framework for running AI and batch workloads on any infra, offering unified execution, high cost savings, and high GPU availability.
<p align="center">
<img src="https://i.imgur.com/xOc6gx3.png" alt="DeepSeek-Janus on SkyPilot" style="width: 70%;">
</p>

On Jan 27, 2025, DeepSeek AI released the [Janus](https://github.com/deepseek-ai/Janus). It outperforms **state-of-the-art Vision Language Models** such as LLaVA, supporting a variety of Vision-Language tasks such as image generation and Q&A. 


This guide walks through how to run and host models **on any infrastructure** from ranging from Local GPU workstation, Kubernetes cluster and public clouds ([15+ clouds supported](https://docs.skypilot.co/en/latest/getting-started/installation.html)). 

### Step 0: Bring any infra

Install SkyPilot on your local machine:

```bash
pip install 'skypilot-nightly[all]'
```

Pick one of the following depending on what infra you want to run:

**If your local machine/cluster has GPU**: you can run SkyPilot [directly on existing machines](https://docs.skypilot.co/en/latest/reservations/existing-machines.html).

**If you want to use Clouds** (15+ clouds are supported):
See [docs](https://docs.skypilot.co/en/latest/getting-started/installation.html) for details.


### Step 1: Run it with SkyPilot

Now it's time to run Janus with SkyPilot. Commands may vary based on the GPUs available to you.  
 

Run Janus (1.5B) with:
```
sky launch janus_1.5b.yaml \
  -c janus \
  --env HF_TOKEN=YOUR_HUGGING_FACE_API_TOKEN 
```

or run Janus Pro (7B) with: 

```
sky launch januspro_7b.yaml \
  -c janus \
  --env HF_TOKEN=YOUR_HUGGING_FACE_API_TOKEN 
```

It will show all the available machines that you have and pricing
```
...

Considered resources (1 node):
-------------------------------------------------------------------------------------------------------
 CLOUD   INSTANCE                   vCPUs   Mem(GB)   ACCELERATORS   REGION/ZONE   COST ($)   CHOSEN   
-------------------------------------------------------------------------------------------------------
 AWS     g6.4xlarge                 16      64        L4:1           us-east-1     1.32          ✔     
 AWS     g5.4xlarge                 16      64        A10G:1         us-east-1     1.62                
 Azure   Standard_NV36ads_A10_v5    36      440       A10:1          eastus        3.20                
 Azure   Standard_NC24ads_A100_v4   24      220       A100-80GB:1    eastus        3.67                
-------------------------------------------------------------------------------------------------------
````

### Step 2: Access the deployed server
You should be able to access directly via a terminal prompt via 
```
Running on public URL: https://xxxxxx.gradio.live
```
or you can access through getting the IP address of the deployed instance via 
```
echo `sky status --ip janus`
```

### Example Prompts
```
A blue sky,  vast fluffy clouds surrounding, enchanting, immortal, dynamic motion, cinematic, Unreal Engine 5 and Octane Render, highly detailed, photorealistic, natural colors, epic atmosphere, and breathtaking realism
```
<p align="center">
<img src="https://i.imgur.com/k4yh5BR.png" alt="DeepSeek-Janus on SkyPilot" style="width: 70%;">
</p>