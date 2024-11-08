variable "region" {
  type    = string
  default = "us-east-1"
}

locals {
  date = formatdate("YYMMDD", timestamp())
}

source "amazon-ebs" "gpu-ubuntu" {
  ami_name      = "skypilot-aws-gpu-ubuntu-${local.date}"
  instance_type = "g6.xlarge"
  region        = var.region
  ssh_username  = "ubuntu"
  source_ami_filter {
    filters = {
      name                = "ubuntu/images/*ubuntu-jammy-22.04-amd64-server-*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    most_recent = true
    owners      = ["099720109477"]
  }
  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 30
    volume_type           = "gp2"
    delete_on_termination = true
  }
}

build {
  name = "aws-gpu-ubuntu-build"
  sources = [
    "source.amazon-ebs.gpu-ubuntu"
  ]
  provisioner "shell" {
    script = "./provisioners/docker.sh"
  }
  provisioner "shell" {
    script = "./provisioners/cuda.sh"
  }
  provisioner "shell" {
    script = "./provisioners/nvidia-container-toolkit.sh"
  }
  provisioner "shell" {
    environment_vars = [
      "CLOUD=aws",
    ]
    script = "./provisioners/skypilot.sh"
  }
  provisioner "shell" {
    script = "./provisioners/user-toolkit.sh"
  }
}
