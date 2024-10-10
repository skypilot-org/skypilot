Speeding Up Replica Setup
=========================

When serving AI models, the setup process like dependencies installation and model weights downloading may take a lot of time. To speed up this process, you can use the :code:`ultra` disk tier:

.. code-block:: yaml
  :emphasize-lines: 7

  service:
    replicas: 2
    readiness_probe: /v1/models
  resources:
    ports: 8080
    accelerators: A10G:8
    disk_tier: ultra

We find that when loading large models, the performance is sometime limited by the disk speed. By using the `ultra` disk tier, you can significantly reduce the time it takes to set up your replicas, allowing for faster response times and improved overall performance. Here is a comparison of disk tiers and their respective speeds. All tests are running on AWS and the result is the end-to-end execution time for launching a Llama 2 70b endpoint with the latest version of vLLM, on an :code:`A10G:8` instance (:code:`g5.48xlarge`).

.. list-table::
   :widths: 10 10
   :header-rows: 1

   * - Disk Tier
     - Speed
   * - :code:`ultra`
     - 410s
   * - :code:`high`
     - 524s