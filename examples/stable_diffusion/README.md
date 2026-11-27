# Stable Diffusion Web UI on SkyPilot

Run the [InvokeAI](https://github.com/invoke-ai/InvokeAI) Stable Diffusion web UI on a cloud GPU with SkyPilot, using the official `ghcr.io/invoke-ai/invokeai:v6.14.2-cuda` image (pinned; newer tags are listed in the [InvokeAI releases](https://github.com/invoke-ai/InvokeAI/releases)).

## Steps

1. Install SkyPilot by following [the instructions](https://docs.skypilot.co/en/latest/getting-started/installation.html).

2. Run `git clone https://github.com/skypilot-org/skypilot.git && cd skypilot/examples/stable_diffusion`

3. Run `sky launch -c stable-diffusion stable_diffusion_docker.yaml`

4. Run `ssh -L 9090:localhost:9090 stable-diffusion`

5. Open [`http://localhost:9090`](http://localhost:9090) in your browser.

6. On first launch, open **Model Manager** in the UI and install a starter model (for example, a Stable Diffusion 1.5 or SDXL checkpoint). Models and generated images are stored under `~/invokeai` on the cluster, so they survive container restarts.

7. Type in a prompt and click **Invoke** to generate images.

8. Once you are done, run `sky stop stable-diffusion` to stop the VM, or `sky down stable-diffusion` to delete it.

If a model you want to install is gated on Hugging Face, pass a token with `sky launch --env HUGGING_FACE_HUB_TOKEN=<token> ...`.

## Tips

- Prompts like `A photo of an astronaut riding a horse, cinematic lighting, highly detailed` work well; use the negative prompt field to exclude things you don't want.
- The **Canvas** tab supports inpainting and outpainting; **Workflows** exposes the node editor for more advanced pipelines.

## Running locally

`docker-compose.yml` starts the same container on a local machine with an NVIDIA GPU: `docker compose up`.
