.. _add-gpu:

============================
Adding a New GPU to SkyPilot
============================

This is a general guide for adding support to a new GPU, on a cloud already
supported by SkyPilot. It's strongly based on our experience
with adding GCP's L4.

**Step 1.** Update the catalog fetcher: make sure your GPU is in the skypilot catalog.

For GCP, this can be done by running
:code:`python -m sky.clouds.service_catalog.data_fetchers.fetch_gcp`
and examining the generated :code:`vms.csv` file. (It might already be
updated. If not, change the code, and be sure to replace your
:code:`.skypilot/catalogs/...../vms.csv` file when testing.)

Also, if the GPU requires a specific type of VM (like A100 or L4 in GCP),
make sure the VM is also in the catalog. For GCP, adding a VM might be as
simple as modifying the :code:`SERIES_TO_DISCRIPTION` variable in
:code:`sky/clouds/service_catalog/data_fetchers/fetch_gcp.py` (see the
      code- you may want to view the output from :code:`get_skus`).

**Step 2.** Update the "service catalog" file. (ex.
:code:`sky/clouds/service_catalog/gcp_catalog.py`)

It helps if there is existing code (for GPUs already supported) that
implements the behavior you want: then you can just copy it/add to it.

For GCP:

* If your GPU requires a specific VM, like A100 and L4 do, look at the
  A100/L4-specific code (involving :code:`_ACC_INSTANCE_TYPE_DICTS`), and
  add to it. Otherwise, consider adding to :code:`_NUM_ACC_TO_NUM_CPU`
  and :code:`_NUM_ACC_TO_MAX_CPU_AND_MEMORY` and so on.

**Step 3.** Update the "clouds" file. (ex. :code:`sky/clouds/gcp.py`)

For GCP:

* in the function :code:`make_deploy_resources_variables`, check that
  :code:`resources_vars['gpu']` is calculated correctly. Also, if you want your VMs
  with your GPU to be launched with a particular default VM image, you can
  specify that.

**Step 4.**
Keep in mind that when making a pull request, some of the tests
may fail if skypilot's catalog repo has not yet been updated, but you can
always run the tests locally. Good luck!
