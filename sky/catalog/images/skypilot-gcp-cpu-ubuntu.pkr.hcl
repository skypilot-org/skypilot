variable "skypilot_src" {
  type        = string
  default     = ""
  description = "pip source to bake SkyPilot from, e.g. git+https://github.com/skypilot-org/skypilot@<sha>. Empty uses the latest skypilot-nightly."
}


locals {
  # UTC, to the minute, so same-day rebuilds get distinct image names.
  date = formatdate("YYMMDDhhmm", timestamp())
}

source "googlecompute" "cpu-ubuntu" {
  project_id          = "sky-dev-465"
  image_name          = "skypilot-gcp-cpu-ubuntu-${local.date}"
  source_image_family = "ubuntu-2204-lts"
  zone                = "us-west1-a"
  image_description   = "SkyPilot custom image for launching GCP CPU instances."
  tags                = ["packer"]
  disk_size           = 10
  machine_type        = "e2-medium"
  ssh_username        = "gcpuser"
}

build {
  name    = "gcp-cpu-ubuntu-build"
  sources = ["sources.googlecompute.cpu-ubuntu"]
  provisioner "shell" {
    script = "./provisioners/docker.sh"
  }
  provisioner "shell" {
    environment_vars = [
      "CLOUD=gcp",
      "SKYPILOT_SRC=${var.skypilot_src}",
    ]
    script = "./provisioners/skypilot.sh"
  }
  provisioner "shell" {
    script = "./provisioners/user-toolkit.sh"
  }
}
