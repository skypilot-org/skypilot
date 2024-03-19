AI Gallery
====================


AI Gallery is a collection of popular AI frameworks, LLM models, and Tutorials.
It aims to speed up the evolution of AI by providing a simple way to package and share all stages of AI using the simple interface of SkyPilot.
Readers can directly take the YAML files and use them to run AI on their own infrastructure.


Contents
--------

.. toctree::
   :maxdepth: 1
   :caption: Inference Engine

   frameworks/vllm
   frameworks/sglang
   frameworks/tgi
   frameworks/lorax
   frameworks/tabby


.. toctree::
   :maxdepth: 1
   :caption: LLM Models
   
   llms/mixtral
   Mistral 7B <https://docs.mistral.ai/self-deployment/skypilot/>
   llms/llama-2
   llms/codellama
   llms/gemma

.. toctree::
   :maxdepth: 1
   :caption: Tutorial

   tutorials/finetuning.md



Contributing
------------
We welcome contributions from the community. If you would like to contribute, please follow the guidelines below.

1. Fork the `SkyPilot repository <https://github.com/skypilot-org/skypilot>`__ on GitHub.
2. Create a new folder for your own framework, LLM model, or Tutorial under `llm/ <https://github.com/skypilot-org/skypilot/tree/master/llm>`.
3. Add your own README, SkyPilot YAML file and the necessary files to run your AI.
4. Create a soft link in `docs/source/_gallery_original <https://github.com/skypilot-org/skypilot/blob/master/docs/source/_gallery_original>`__ to the README file in one of the subfolders (frameworks, llms, tutorials), e.g., :code:`cd docs/source/_gallery_original/llms; ln -s ../../llm/mixtral/README.md mixtral.md`.
5. Add the file path to the `toctree` above.
6. Create a pull request to the `SkyPilot repository <https://github.com/skypilot-org/skypilot/compare>`__.

If you have any questions, please feel free to ask in the `SkyPilot Slack <https://slack.skypilot.co>`__.

