.. _recipes:

SkyPilot Recipes
================

Recipes allow storing, sharing and reusing SkyPilot YAMLs across users. With Recipes, you can:

- **Reuse patterns across workflows**: Create recipes for clusters, managed jobs, pools, and volumes
- **Launch quickly**: Start from a copyable CLI command (e.g., ``sky launch recipes:dev-cluster``) - no YAML file required
- **Edit in place**: View and modify recipes directly in the dashboard
- **Share templates across your organization**: Customize your YAMLs once, then share institutional setup across your team


Getting started
---------------

Recipes are managed through the SkyPilot dashboard. To access Recipes:

1. Run ``sky dashboard``
2. Navigate to the **Recipes** section in the navbar

.. image:: /images/recipes-page.png
   :alt: SkyPilot Recipes page in the dashboard
   :align: center

.. raw:: html

   <div style="margin-top: 10px;"></div>

Recipes support these SkyPilot resource types: **Clusters**, **Managed Jobs**, **Pools**, and **Volumes**.

Creating a recipe
-----------------

To create a new recipe in the dashboard:

1. Click **New Recipe** in the Recipes page
2. Enter a name and description for your recipe
3. Select the resource type (cluster, managed job, pool, or volume)
4. Write or paste your YAML configuration
5. Click **Create Recipe** to store the recipe

.. image:: /images/recipes-create-form.png
   :alt: Create recipe form in the dashboard
   :align: center

.. note::
   **File and Code Storage Limitations**

   Recipes do not support local file uploads. For code and data:

   - **Code**: Use Git repositories with :ref:`workdir <sync-code-and-project-files-git>` dictionary format:

     .. code-block:: yaml

        workdir:
          url: https://github.com/org/repo

   - **Data**: Use cloud storage buckets with :ref:`file_mounts <file-mounts-example>` (e.g., ``s3://bucket``, ``gs://bucket``) or :ref:`volumes <volumes-all>` for persistent storage
   - Local paths (``workdir: .``, ``file_mounts: ~/local-dir``) are not supported

**Copying from an Existing Recipe**

To create a recipe based on an existing one:

1. Open the recipe you want to copy
2. Click **Copy to New**
3. Modify the name, description, and YAML as needed
4. Click **Create Recipe** to create the new recipe

This is useful for creating variations of a recipe (e.g., different GPU types or environment versions).

Launching from recipes
----------------------

The most convenient way to use recipes is through the CLI with the ``recipes:`` prefix:

.. code-block:: bash

   # Launch a cluster from a recipe
   sky launch recipes:dev-cluster

   # Launch with custom cluster name
   sky launch -c my-dev recipes:dev-cluster

   # Launch a managed job from a recipe
   sky jobs launch recipes:training-job

No YAML file needed - the configuration is fetched directly from the SkyPilot API server!

You can override the recipe fields with CLI args:

.. code-block:: bash

   # Launch with overrides
   sky launch recipes:gpu-cluster --cpus 16 --gpus H100:4 --env DATA_PATH=s3://my-data --secret HF_TOKEN


.. tip::

   You can find the launch command for your recipe in the dashboard:

   .. image:: /images/recipes-launch-command.png
      :alt: Copy launch command from recipe detail page
      :align: center


Managing recipes
----------------

All recipes are listed in the **Recipes** page of the dashboard. Click on any recipe to view its details:

- **YAML Configuration**: The full task specification
- **Launch Command**: Copy-paste command to launch the recipe
- **Metadata**: Name, description, type, creator, and update time

.. image:: /images/recipes-detail-view.png
   :alt: Recipe detail view showing YAML and launch command
   :align: center

To **edit** a recipe, open it and click **Edit**, then update the YAML, name, or description. Changes are saved immediately and available to all users.

To **delete** a recipe, open it and click **Delete**, then confirm. Note that deleting a recipe is permanent and cannot be undone. Existing clusters or jobs launched from the recipe will continue running.

Sharing recipes across your team
---------------------------------

A key benefit of Recipes is enabling teams to share standardized configurations. Here are some common patterns:

.. tab-set::

    .. tab-item:: Development Environment

        Standard onboarding setup for new team members.

        .. code-block:: yaml

            name: dev-env

            resources:
              cpus: 8+
              memory: 32+
              disk_size: 100

            setup: |
              # Install common tools
              conda create -n dev python=3.11 -y
              conda activate dev
              pip install ipython jupyter black pytest

            run: |
              echo "Development environment ready!"
              echo "Connect with: ssh dev-env"

        **Use case:** New engineers can get started immediately: ``sky launch recipes:dev-env``

    .. tab-item:: GPU Training Cluster

        Production training configuration with A100 GPUs.

        .. code-block:: yaml

            name: gpu-training

            resources:
              accelerators: A100:8
              memory: 128+
              disk_size: 500

            setup: |
              conda create -n train python=3.10 -y
              conda activate train
              pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
              pip install transformers datasets wandb

            run: |
              nvidia-smi
              python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"

        **Use case:** Everyone uses the same training configuration: ``sky launch recipes:gpu-training``

    .. tab-item:: Batch Processing Job

        Cost-optimized data processing on spot instances.

        .. code-block:: yaml

            name: batch-process

            resources:
              cpus: 16+
              memory: 64+
              use_spot: true

            file_mounts:
              /data:
                source: s3://my-data-bucket
                mode: MOUNT
              /output:
                source: s3://my-output-bucket
                mode: MOUNT

            volumes:
              /checkpoint: my-checkpoint-vol

            setup: |
              pip install pandas pyarrow

            run: |
              python process_data.py --input /data --output /output --checkpoint /checkpoint

        **Use case:** Standardized batch processing with automatic recovery: ``sky jobs launch recipes:batch-process``

    .. tab-item:: Jupyter Notebook Server

        Shared data science environment.

        .. code-block:: yaml

            name: jupyter-server

            resources:
              cpus: 8+
              memory: 32+
              disk_size: 100
              ports: 8888

            setup: |
              conda create -n jupyter python=3.10 -y
              conda activate jupyter
              pip install jupyterlab pandas numpy scikit-learn matplotlib seaborn

            run: |
              conda activate jupyter
              jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root

        **Use case:** Team data exploration with consistent libraries: ``sky launch recipes:jupyter-server``


Best practices
--------------

**Naming:** Use clear, descriptive names like ``dev-cluster-python311``, ``training-a100-8gpu-pytorch``, or ``inference-server-vllm``. Avoid generic names like ``test`` or ``recipe1``. Include environment or version information when relevant (``prod-inference-v2``, ``staging-training``).

**Documentation:** Always include a description explaining what the recipe is for, when to use it, who the intended user is, and any special requirements. Example: *"GPU training cluster with PyTorch 2.0 and 8x A100 GPUs. Use for large model training. Requires S3 data bucket configured. Costs ~$20/hour."*

**Configuration tips:**

- Use environment variables for flexibility:

  .. code-block:: yaml

     run: |
       python train.py --data $DATA_PATH --epochs $EPOCHS

  Then customize at launch: ``sky launch recipes:training --env DATA_PATH=s3://my-data``

- Use :ref:`secrets <yaml-spec-secrets>` for sensitive values like API keys — they work like environment variables but are redacted in the dashboard:

  .. code-block:: yaml

     secrets:
       HF_TOKEN: null
       WANDB_API_KEY: null

     run: |
       huggingface-cli login --token $HF_TOKEN
       python train.py

  Then pass secrets at launch: ``sky launch recipes:training --secret HF_TOKEN --secret WANDB_API_KEY``

- Separate ``setup`` (one-time installation) from ``run`` (actual workload) for reusability

