variable "client_secret" {
  type        = string
  description = "The client secret for the packer client registered in Azure (see Azure app registration)"
}

variable "vm_generation" {
  type        = number
  description = "Azure's VM generation, currently support 1 or 2"
}

variable "use_grid_driver" {
  type        = bool
  default     = false
  description = "Whether to use the Azure GRID driver. Currently only A10 GPU VMs need this."
}

locals {
  date    = formatdate("YYMMDD", timestamp())
  version = formatdate("YY.MM.DD", timestamp())
}

source "azure-arm" "gpu-ubuntu" {
  managed_image_resource_group_name = "skypilot-images"

  subscription_id = "59d8c23c-7ef5-42c7-b2f3-a919ad8026a7"
  tenant_id       = "7c81f068-46f8-4b26-9a46-2fbec2287e3d"
  client_id       = "1d249f23-c22e-4d02-b62b-a6827bd113fe"
  client_secret   = var.client_secret

  os_type         = "Linux"
  image_publisher = "Canonical"
  image_offer     = "0001-com-ubuntu-server-jammy"
  image_sku       = var.vm_generation == 1 ? "22_04-lts" : "22_04-lts-gen2"
  location        = var.use_grid_driver || var.vm_generation == 1 ? "eastus" : "centralus"
  vm_size         = var.use_grid_driver ? "Standard_NV12ads_A10_v5" : (var.vm_generation == 1 ? "Standard_NC4as_T4_v3" : "Standard_NC24ads_A100_v4")
  ssh_username    = "azureuser"
  azure_tags = {
    Created_by = "packer"
    Purpose    = "skypilot"
  }

  shared_image_gallery_destination {
    subscription   = "59d8c23c-7ef5-42c7-b2f3-a919ad8026a7"
    resource_group = "skypilot-images"
    gallery_name   = var.use_grid_driver || var.vm_generation == 1 ? "skypilot_images" : "skypilot_image_gallery"
    image_name     = var.use_grid_driver ? "skypilot-gpu-gen2-grid" : "skypilot-gpu-gen${var.vm_generation}"
    image_version  = "${local.version}"
    replication_regions = [
      "centralus",
      "eastus",
      "eastus2",
      "northcentralus",
      "southcentralus",
      "westcentralus",
      "westus",
      "westus2",
      "westus3"
    ]
  }
}

build {
  name    = "azure-gpu-ubuntu-build"
  sources = ["sources.azure-arm.gpu-ubuntu"]
  provisioner "shell" {
    script = "./provisioners/docker.sh"
  }
  provisioner "shell" {
    script = var.use_grid_driver ? "./provisioners/cuda-azure-grid.sh" : "./provisioners/cuda.sh"
  }
  provisioner "shell" {
    script = "./provisioners/nvidia-container-toolkit.sh"
  }
  provisioner "shell" {
    environment_vars = [
      "CLOUD=azure",
    ]
    script = "./provisioners/skypilot.sh"
  }
  provisioner "shell" {
    environment_vars = [
      var.use_grid_driver ? "AZURE_GRID_DRIVER=1" : "AZURE_GRID_DRIVER=0",
    ]
    script = "./provisioners/user-toolkit.sh"
  }
}
