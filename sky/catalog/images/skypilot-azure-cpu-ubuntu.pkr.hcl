variable "client_secret" {
  type        = string
  description = "The client secret for the packer client registered in Azure (see Azure app registration)"
}

variable "vm_generation" {
  type        = number
  description = "Azure's VM generation, currently support 1 or 2"
}

locals {
  date    = formatdate("YYMMDD", timestamp())
  version = formatdate("YY.MM.DD", timestamp())
}

source "azure-arm" "cpu-ubuntu" {
  managed_image_resource_group_name = "skypilot-images"

  subscription_id = "59d8c23c-7ef5-42c7-b2f3-a919ad8026a7"
  tenant_id       = "7c81f068-46f8-4b26-9a46-2fbec2287e3d"
  client_id       = "1d249f23-c22e-4d02-b62b-a6827bd113fe"
  client_secret   = var.client_secret

  os_type         = "Linux"
  image_publisher = "Canonical"
  image_offer     = "0001-com-ubuntu-server-jammy"
  image_sku       = var.vm_generation == 1 ? "22_04-lts" : "22_04-lts-gen2"
  location        = "centralus"
  vm_size         = var.vm_generation == 1 ? "Standard_D1_v2" : "Standard_B2s"
  ssh_username    = "azureuser"
  azure_tags = {
    Created_by = "packer"
    Purpose    = "skypilot"
  }

  shared_image_gallery_destination {
    subscription   = "59d8c23c-7ef5-42c7-b2f3-a919ad8026a7"
    resource_group = "skypilot-images"
    gallery_name   = "skypilot_image_gallery"
    image_name     = "skypilot-cpu-gen${var.vm_generation}"
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
  name    = "azure-cpu-ubuntu-build"
  sources = ["sources.azure-arm.cpu-ubuntu"]
  provisioner "shell" {
    script = "./provisioners/docker.sh"
  }
  provisioner "shell" {
    environment_vars = [
      "CLOUD=azure",
    ]
    script = "./provisioners/skypilot.sh"
  }
  provisioner "shell" {
    script = "./provisioners/user-toolkit.sh"
  }
}
