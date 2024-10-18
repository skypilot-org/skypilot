
locals {
  timestamp = regex_replace(timestamp(), "[- TZ:]", "")
}

source "googlecompute" "cpu-ubuntu" {
  project_id          = "sky-dev-465"
  image_name          = "skypilot-gcp-cpu-ubuntu-${local.timestamp}"
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
    script = "./provisioners/skypilot.sh"
  }
  provisioner "shell" {
    environment_vars = [
      "CLOUD=gcp",
    ]
    script = "./provisioners/cloud.sh"
  }
}
