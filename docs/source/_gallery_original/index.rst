AI Gallery
====================


AI Gallery is a collection of ready-to-run recipes for popular AI frameworks and AI models.
It provides a simple way to **package**, **share**, and **distribute** AI projects using the simple interface of SkyPilot.

Readers can directly execute the SkyPilot YAMLs on their own infrastructure, such as cloud VMs or Kubernetes.

.. image:: https://imgur.com/dA0Llxh.png
   :alt: AI Gallery
   :align: center

Contents
--------

.. toctree::
   :maxdepth: 1
   :caption: Inference Engines

   vLLM <frameworks/vllm>
   Hugging Face TGI <frameworks/tgi>
   SGLang <frameworks/sglang>
   LoRAX <frameworks/lorax>


.. toctree::
   :maxdepth: 1
   :caption: LLM Models
   
   Mixtral (Mistral AI) <llms/mixtral>
   Mistral 7B (Mistral AI) <https://docs.mistral.ai/self-deployment/skypilot/>
   Llama-2 (Meta) <llms/llama-2>
   CodeLlama (Meta) <llms/codellama>
   Gemma (Google) <llms/gemma>

.. toctree::
   :maxdepth: 1
   :caption: Applications

   Tabby: Coding Assistant <applications/tabby>
   LocalGPT: Chat with PDF <applications/localgpt>

.. toctree::
   :maxdepth: 1
   :caption: Tutorials

   tutorials/finetuning.md



Contributing
------------
We welcome contributions from the community. If you would like to contribute, please follow the guidelines below.

1. Fork the `SkyPilot repository <https://github.com/skypilot-org/skypilot>`__ on GitHub.
2. Create a new folder for your own framework, LLM model, or Tutorial under `llm/ <https://github.com/skypilot-org/skypilot/tree/master/llm>`__.
3. Add your own README, SkyPilot YAML file and the necessary files to run your AI.
4. Create a soft link in `docs/source/_gallery_original <https://github.com/skypilot-org/skypilot/blob/master/docs/source/_gallery_original>`__ to the README file in one of the subfolders (frameworks, llms, tutorials), e.g., :code:`cd docs/source/_gallery_original/llms; ln -s ../../llm/mixtral/README.md mixtral.md`.
5. Add the file path to the `toctree` above.
6. Create a pull request to the `SkyPilot repository <https://github.com/skypilot-org/skypilot/compare>`__.

If you have any questions, please feel free to ask in the `SkyPilot Slack <https://slack.skypilot.co>`__.

