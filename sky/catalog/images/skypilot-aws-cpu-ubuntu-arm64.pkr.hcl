variable "skypilot_src" {
  type        = string
  default     = ""
  description = "pip source to bake SkyPilot from, e.g. git+https://github.com/skypilot-org/skypilot@<sha>. Empty uses the latest skypilot-nightly."
}

variable "region" {
  type    = string
  default = "us-east-1"
}

locals {
  # UTC, to the minute, so same-day rebuilds get distinct image names.
  date = formatdate("YYMMDDhhmm", timestamp())
}

source "amazon-ebs" "cpu-ubuntu-arm64" {
  ami_name      = "skypilot-aws-cpu-ubuntu-arm64-${local.date}"
  instance_type = "t4g.micro"
  region        = var.region
  ssh_username  = "ubuntu"
  source_ami_filter {
    filters = {
      name                = "ubuntu/images/*ubuntu-jammy-22.04-arm64-server-*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    most_recent = true
    owners      = ["099720109477"]
  }
  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 8
    volume_type           = "gp3"
    delete_on_termination = true
  }
}

build {
  name    = "aws-cpu-ubuntu-arm64-build"
  sources = ["sources.amazon-ebs.cpu-ubuntu-arm64"]
  provisioner "shell" {
    script = "./provisioners/docker.sh"
  }
  provisioner "shell" {
    environment_vars = [
      "CLOUD=aws",
      "SKYPILOT_SRC=${var.skypilot_src}",
    ]
    script = "./provisioners/skypilot.sh"
  }
  provisioner "shell" {
    script = "./provisioners/user-toolkit.sh"
  }
}
