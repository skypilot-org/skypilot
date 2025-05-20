.. _quota:

Requesting Quota Increase
=========================


Most cloud providers enforce a quota policy to limit the number of VM instances that can exist in a given region.
Users may encounter `QuotaExceeded` or `VcpuLimitExceeded` errors during resources provisioning, especially for high end GPUs such as H100/A100.
To check or increase your quota limits, please follow the below instructions.
After submitting the request, it will usually take a few days for the support team to review.
To increase chances of being approved, you may respond their inquiry emails on how the requested resources will be used your projects.

.. _quota-aws:

AWS
---

1. Go to the `EC2 Quotas console <https://console.aws.amazon.com/servicequotas/home/services/ec2/quotas>`_.
2. **Select a region** on the top right.
3. Choose an EC2 instance type from the list (e.g, ``Running On-Demand P instances`` or ``All P Spot Instance Requests``). Use ``sky show-gpus --infra aws --all`` or check `here <https://aws.amazon.com/ec2/instance-types/>`__ for more instance types.
4. Click the quota name, and then choose **Request quota increase**.
5. For **Change quota value**, enter the new value.
6. Choose **Request**.

Azure
-----

1. First go to `Azure's quota page <https://portal.azure.com/#blade/Microsoft_Azure_Capacity/QuotaMenuBlade/myQuotas>`_.
2. Select **Request Increase** near the top of the screen.
3. For Quota type, select ``Compute-VM (cores-vCPUs) subscription limit increases``. Hint: note that a message "Get more quota now. You don’t need a support ticket to get more quota..." may pop up; feel free to skip it as requesting quotas for most GPU instances still requires creating support tickets (next steps).
4. Select **Next** to go to the Additional details screen, then select **Enter details**.

  - In the Quota details screen:
  - For Deployment model, ensure **Resource Manager** is selected.
  - For Locations, select all regions in which you want to increase quotas.
  - For each region you selected, select one or more VM series from the Quotas drop-down list.
  - For each VM Series you selected (e.g., ``ND_H100_v5`` for H100 instances), enter the new vCPU limit that you want for this subscription. You may check `for more VM Series <https://docs.microsoft.com/en-us/azure/virtual-machines/sizes-gpu>`_.
  - When you're finished, select **Save and continue**.

5. Enter or confirm your contact details, then select **Next**.
6. Finally, ensure that everything looks correct on the Review + create page, then select **Create** to submit your request.

GCP
---

1. In the Google Cloud Console, go to the `Quota page <https://console.cloud.google.com/iam-admin/quotas/>`_.
2. Click **Filter** and select ``Service: Compute Engine API``.
3. For H100 GPUs: choose ``metric: GPUS_PER_GPU_FAMILY`` and select dimension ``gpu_family: NVIDIA_H100``.
4. For all other GPUs: choose ``Limit Name: instance_name``. (e.g., ``NVIDIA-V100-GPUS-per-project-region``). You may check the `the compute GPU list <https://cloud.google.com/compute/quotas#gpu_quota>`_.
5. Select the checkbox of the region whose quota you want to change.
6. Click **Edit Quotas** and fill out the new limit.
7. Click **Submit Request**.

OCI
-------------------------------

1. Go to the `OCI Limits, Quotas and Usage console <https://cloud.oracle.com/limits>`_ to check your current resources status.
2. Click the **request a service limit increase** link on the page if you want to increase quotas.
3. Choose a **Service Category** from the list (e.g, ``Compute``). 
4. Choose a **Resource** from the list (e.g, ``GPUs for GPU.A10 based VM and BM Instances``). Use ``sky show-gpus --infra oci --all`` or check `here <https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm>`__ for more instance types.
5. Enter the **Limit** field for your new limit and **Reason for request** for justification.
6. Click **Create Support Request** to submit.
7. You may check `OCI Service Limits <https://docs.oracle.com/en-us/iaas/Content/General/Concepts/servicelimits.htm#computelimits>`_ for more information.
