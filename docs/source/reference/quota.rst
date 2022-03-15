.. _quota:
Quota Increase
==================


Most cloud providers enforce a quota policy to limit the number of VM instances that can exist in a given region.
Users may encourter `QuotaExceeded` or `VcpuLimitExceeded` errors during resources provisioning, especially for high end GPUs such as V100/A100.
To check or increase your quota limits, please follow the below instructions.
After submitting the request, it will usually take a few days for the support team to review.
To increase chances of being approved, you may respond their inquiry emails on how the requested resources will be used your projects. 

AWS
-------------------------------

1. Go to the `EC2 Quotas console <https://console.aws.amazon.com/servicequotas/home/services/ec2/quotas>`_.
2. **Select a region** on the top right.
3. Choose an EC2 instance type from the list (e.g, ``Running On-Demand P instances``). You may check `here <https://aws.amazon.com/ec2/instance-types/>`_ for more instance types.
4. Click the instance name, and then choose **Request quota increase**.
5. For **Change quota value**, enter the new value.
6. Choose **Request**.

Azure
-------------------------------

1. First go to Azure's `quota <https://portal.azure.com/#blade/Microsoft_Azure_Capacity/QuotaMenuBlade/myQuotas>`_ page.
2. Select **Request Increase** near the top of the screen.
3. For Quota type, select ``Compute-VM (cores-vCPUs) subscription limit increases``.
4. Select **Next** to go to the Additional details screen, then select **Enter details**.
  - In the Quota details screen:
  - For Deployment model, ensure **Resource Manager** is selected.
  - For Locations, select all regions in which you want to increase quotas.
  - For each region you selected, select one or more VM series from the Quotas drop-down list.
  - For each VM Series you selected (e.g., ``NCSv3``, ``NDv2`` for V100 instances), enter the new vCPU limit that you want for this subscription. You may check `here <https://docs.microsoft.com/en-us/azure/virtual-machines/sizes-gpu>`_ for more VM Series.
  - When you're finished, select **Save and continue**.
5. Enter or confirm your contact details, then select **Next**.
6. Finally, ensure that everything looks correct on the Review + create page, then select **Create** to submit your request.

GCP
-------------------------------

1. In the Google Cloud Console, go to the `Quota <https://console.cloud.google.com/iam-admin/quotas/>`_ page.
2. Click **Filter** and select ``Service: Compute Engine API``.
3. Choose ``Limit Name: instance_name``. (e.g., ``NVIDIA-V100-GPUS-per-project-region``). You may check `here <https://cloud.google.com/compute/quotas#gpu_quota>`_ for a complete GPU list. 
4. Select the checkbox of the region whose quota you want to change.
5. Click **Edit Quotas** and fill out the new limit.
6. Click **Submit Request**.