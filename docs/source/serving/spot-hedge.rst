.. _spot-hedge:

Spot Hedge
==========

SpotHedge is a efficient policy that leverages spot replicas across different failure domains (e.g., regions and clouds) to ensure availability, lower costs, and high service quality. SpotHedge intelligently spreads spot replicas across different regions or clouds to improve availability and reduce correlated preemptions, overprovisions cheap spot replicas than required as a safeguard against possible preemptions, and dynamically falls back to on-demand replicas when spot replicas become unavailable.

For more information, see `SkyServe: Serving AI Models across Regions and Clouds with Spot Instances <https://arxiv.org/abs/2411.01438>`_.

Configuration
-------------

To enable SpotHedge, you need to set the following configuration:

.. code-block:: yaml
  :emphasize-lines: 7-9,19

  # spot-hedge.yaml
  service:
    replica_policy:
      min_replicas: 2
      max_replicas: 5
      target_qps_per_replica: 10
      num_overprovision: 1
      dynamic_ondemand_fallback: true
      spot_placer: dynamic_fallback

  # Optionally: specify the regions to deploy replicas.
  resources:
    any_of:
      # Enable one region in AWS.
      - cloud: aws
        region: us-east-1
      # Enable all regions in GCP.
      - cloud: gcp
    use_spot: true
    accelerators: L4
    cpus: 32+
    ports: 8081

  envs:
    MODEL_NAME: meta-llama/Meta-Llama-3.1-8B-Instruct
    HF_TOKEN: # TODO: Fill with your own huggingface token, or use --env to pass.

  setup: |
    pip install vllm==0.5.3post1
    pip install vllm-flash-attn==2.5.9.post1

  run: |
    vllm serve $MODEL_NAME \
      --tensor-parallel-size $SKYPILOT_NUM_GPUS_PER_NODE \
      --max-model-len 4096 \
      --port 8081 \
      2>&1 | tee api_server.log

:code:`num_overprovision` is the number of overprovisioned replicas. :code:`dynamic_ondemand_fallback` is a boolean flag that determines whether to dynamically fall back to on-demand replicas when spot replicas become unavailable. :code:`spot_placer` is the placer to use for spot replicas. When all of them are specified, SpotHedge is enabled.

