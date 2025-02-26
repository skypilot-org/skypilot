Examples
====================

A collection of examples demonstrating the use cases of SkyPilot.
Code samples for the examples can be found in https://github.com/skypilot-org/skypilot/tree/master/examples.

AI Gallery is a collection of ready-to-run recipes for popular AI frameworks and AI models.
It provides a simple way to **package**, **share**, and **run** AI projects using the simple interface of SkyPilot.

You can directly run these recipes on your own infrastructure, such as :ref:`cloud VMs <installation>` or :ref:`Kubernetes <kubernetes-overview>`.

.. image:: ../images/ai-gallery-cover.png
   :alt: AI Gallery
   :align: center

We welcome contributions from the community. If you would like to contribute, please follow the guidelines below.

1. Fork the `SkyPilot repository <https://github.com/skypilot-org/skypilot>`__ on GitHub.
2. Create a new folder for your own framework, LLM model, or tutorial under `llm/ <https://github.com/skypilot-org/skypilot/tree/master/llm>`__.
3. Add a README.md, a SkyPilot YAML, and other necessary files to run the AI project.
4. Create a soft link to your README in `docs/source/_gallery_original <https://github.com/skypilot-org/skypilot/blob/master/docs/source/_gallery_original>`__ to the README file in one of the subfolders (frameworks, llms, tutorials), e.g., :code:`cd docs/source/_gallery_original/llms; ln -s ../../../../llm/mixtral/README.md mixtral.md`.
5. Add the file path to the ``toctree`` above on this page.
6. Create a pull request to the `SkyPilot repository <https://github.com/skypilot-org/skypilot/compare>`__.

If you have any questions, please feel free to ask in the `SkyPilot Slack <https://slack.skypilot.co>`__.





.. toctree::
   :maxdepth: 2

   Quickstart: PyTorch <../getting-started/tutorial>
   Training <../gallery/tutorials/index>
   Serving <../gallery/llms/index>
   Other Frameworks <frameworks/index>
   AI Applications <../gallery/applications/index>
