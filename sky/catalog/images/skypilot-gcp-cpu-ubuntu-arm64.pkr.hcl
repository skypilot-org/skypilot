locals {
  date = formatdate("YYMMDD", timestamp())
}

source "googlecompute" "cpu-ubuntu-arm64" {
  project_id          = "sky-dev-465"
  image_name          = "skypilot-gcp-cpu-ubuntu-arm64-${local.date}"
  source_image_family = "ubuntu-2204-lts-arm64"
  zone                = "us-central1-a"
  image_description   = "SkyPilot custom image for launching GCP Arm CPU instances."
  tags                = ["packer"]
  disk_size           = 10
  machine_type        = "t2a-standard-1"
  ssh_username        = "gcpuser"
}

build {
  name    = "gcp-cpu-ubuntu-arm64-build"
  sources = ["sources.googlecompute.cpu-ubuntu-arm64"]
  provisioner "shell" {
    script = "./provisioners/docker.sh"
  }
  provisioner "shell" {
    environment_vars = [
      "CLOUD=gcp",
    ]
    script = "./provisioners/skypilot.sh"
  }
  provisioner "shell" {
    script = "./provisioners/user-toolkit.sh"
  }
}
