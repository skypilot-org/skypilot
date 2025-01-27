# Run and Serve Janus by DeepSeek with SkyPilot

> SkyPilot is a framework for running AI and batch workloads on any infra, offering unified execution, high cost savings, and high GPU availability.
<p align="center">
<img src="https://i.imgur.com/PAYaKCD.png" alt="DeepSeek-R1 on SkyPilot" style="width: 70%;">
</p>

On Jan 27, 2025, DeepSeek AI released the [Janus](https://github.com/deepseek-ai/Janus). It outperforms **state-of-the-art Vision Languag Models** such as LLaVA, supporting a vareity of Vision-Langauge tasks such as image generation and Q&A. 

This guide walks through how to run and host models **on any infrastructure** from ranging from Local GPU workstation, Kubernetes cluster and public Clouds ([15+ clouds supported](https://docs.skypilot.co/en/latest/getting-started/installation.html)). 

### Step 0: Bring any infra

Install SkyPilot on your local machine:

```bash
pip install 'skypilot-nightly[all]'
```

Pick one of the following depending on what infra you want to run:

**If your local machine/cluster has GPU**: you can run SkyPilot [directly on existing machines](https://docs.skypilot.co/en/latest/reservations/existing-machines.html) with 

**If you want to use Clouds** (15+ clouds are supported):
See [docs](https://docs.skypilot.co/en/latest/getting-started/installation.html) for details.


### Step 1: Run it with SkyPilot

Now it's time to run Janus with SkyPilot. The instruction can be dependent on your existing hardware.  

```
sky launch janus_1.5b.yaml \
  -c janus \
  --env HF_TOKEN=YOUR_HUGGING_FACE_API_TOKEN \
  --gpus L4:1
```

or 

```
sky launch januspro_7b.yaml \
  -c janus \
  --env HF_TOKEN=YOUR_HUGGING_FACE_API_TOKEN \
  --gpus L4:1
```

### Step 2: Access the deployed server
You should be able to access directly via a terminal prompt via 
```
Running on public URL: https://xxxxxx.gradio.live
```
or you can access through getting the IP address of the deployed instance via 
```
echo `sky status --ip janus`
```

### Check out our prompts! 
```
A modern fighter jet soaring through a vast, vibrant blue sky filled with fluffy white clouds. The jet leaves behind a sleek contrail as it cuts through the atmosphere. The sunlight reflects off the jet's metallic surface, highlighting its aerodynamic design. The pilot, wearing a high-tech flight suit and helmet with a reflective visor, gazes into the horizon. The sky transitions from a deep azure at the top to a soft pastel near the horizon, creating a stunning gradient. Cinematic lighting, ultra-realistic details, and dynamic motion blur effects.
```
<p align="center">
<img src="https://i.imgur.com/totgBGb.png" alt="DeepSeek-R1 on SkyPilot" style="width: 70%;">
</p>
