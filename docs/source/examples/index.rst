.. _examples:

Examples
====================

A collection of examples demonstrating the use cases of SkyPilot.

.. grid:: 1 1 1 1
    :gutter: 3

    .. grid-item-card::  👋 New to SkyPilot?
        :link: quickstart
        :link-type: ref
        :text-align: center

        Click here to read Quickstart first.

.. toctree::
   :maxdepth: 2

   Quickstart: PyTorch <../getting-started/tutorial>
   Training <training/index>
   Serving <serving/index>
   Models <models/index>
   AI Applications <applications/index>
   AI Performance <performance/index>
   Orchestrators <orchestrators/index>
   Other Frameworks <frameworks/index>

.. _examples-contribute:

Adding an example
====================

We welcome contributions from the community. Instructions below:

.. admonition:: Adding an example
   :class: dropdown

   1. Fork the `SkyPilot repository <https://github.com/skypilot-org/skypilot>`__ on GitHub.
   2. Create a new folder under `llm/ <https://github.com/skypilot-org/skypilot/tree/master/llm>`__ or `examples/ <https://github.com/skypilot-org/skypilot/tree/master/examples>`__.

      - For example, call it ``my-llm/``.
      - Add a README.md, a SkyPilot YAML, and other necessary files to run the AI project.
      - ``git add`` the files.

   4. Go to the appropriate subdir under Examples and add your example.

      - For example, if you want to add to "Examples / Serving":
      - ``cd docs/source/examples/serving; ln -s ../../generated-examples/my-llm.md``.
      - Add a heading for your example to the TOC in the index.rst file.
      - ``git add index.rst my-llm.md``

   5. Build the docs as usual.
