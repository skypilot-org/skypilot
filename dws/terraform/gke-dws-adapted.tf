# Variables that need to be customized for your project
variable "project_id" {
  description = "Your GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for the cluster"
  type        = string
  default     = "us-east4"
}

variable "cluster_name" {
  description = "Name of the GKE cluster"
  type        = string
  default     = "skypilot-gke"
}

variable "vpc_name" {
  description = "Name of the VPC network"
  type        = string
  default     = "default"
}

variable "subnet_name" {
  description = "Name of the subnet for nodes"
  type        = string
  default     = "default"
}

# Data sources
data "google_project" "project" {
  project_id = var.project_id
}

data "google_compute_zones" "available" {
  region = var.region
}

# Use the default compute service account
# The default compute service account has the format: PROJECT_NUMBER-compute@developer.gserviceaccount.com
locals {
  default_compute_service_account = "${data.google_project.project.number}-compute@developer.gserviceaccount.com"
  gke_service_account = "service-${data.google_project.project.number}@container-engine-robot.iam.gserviceaccount.com"
}

# Create KMS key for cluster encryption (optional - can be removed if not needed)
# Commenting out KMS resources to avoid key version destruction issues
# resource "google_kms_key_ring" "gke_keyring" {
#   name     = "${var.cluster_name}-keyring"
#   location = var.region
# }

# resource "google_kms_crypto_key" "gke_key" {
#   name     = "${var.cluster_name}-key"
#   key_ring = google_kms_key_ring.gke_keyring.id
# }

# # Grant GKE service account permissions to use the KMS key
# resource "google_kms_crypto_key_iam_binding" "gke_key_binding" {
#   crypto_key_id = google_kms_crypto_key.gke_key.id
#   role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"

#   members = [
#     "serviceAccount:${local.gke_service_account}",
#   ]
# }

# Check for GPU availability in zones
data "google_compute_machine_types" "a3-ultragpu-8g" {
  for_each = toset(data.google_compute_zones.available.names)
  zone     = each.value
  filter   = "name = a3-ultragpu-8g"
}

locals {
  # Filter zones that have the required GPU machine type
  gpu_zones = [for zone, data in data.google_compute_machine_types.a3-ultragpu-8g : zone if length(data.machine_types) > 0]
}

resource "google_container_cluster" "gke" {
  name           = var.cluster_name
  description    = "SkyPilot GKE Cluster"
  location       = var.region
  node_locations = local.gpu_zones

  min_master_version = "1.33.1-gke.1744000"
  release_channel {
    channel = "RAPID"
  }

  network    = var.vpc_name
  subnetwork = var.subnet_name

  # Basic IP allocation - you may need to configure secondary ranges
  ip_allocation_policy {
    # If you have secondary ranges configured, specify them here
    # cluster_secondary_range_name  = "pods"
    # services_secondary_range_name = "services"
    stack_type = "IPV4"
  }

  maintenance_policy {
    recurring_window {
      start_time = "2024-08-06T16:00:00Z"
      end_time   = "2024-08-07T04:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SA"
    }
  }

  datapath_provider           = "ADVANCED_DATAPATH"
  default_max_pods_per_node   = 32
  enable_intranode_visibility = true
  enable_l4_ilb_subsetting    = true
  enable_multi_networking     = true
  enable_shielded_nodes       = true

  deletion_protection = false

  # We can't create a cluster with no node pool defined, but we want to only use
  # separately managed node pools. So we create the smallest possible default
  # node pool and immediately delete it.
  remove_default_node_pool = true
  initial_node_count       = 1

  node_config {
    service_account = local.default_compute_service_account

    gcfs_config {
      enabled = true
    }

    gvnic {
      enabled = true
    }
  }

  addons_config {
    gce_persistent_disk_csi_driver_config {
      enabled = true
    }
    gcs_fuse_csi_driver_config {
      enabled = false
    }
    horizontal_pod_autoscaling {
      disabled = false
    }
    http_load_balancing {
      disabled = false
    }
  }

  cost_management_config {
    enabled = true
  }

  # database_encryption {
  #   key_name = google_kms_crypto_key.gke_key.id
  #   state    = "ENCRYPTED"
  # }

  # depends_on = [
  #   google_kms_crypto_key_iam_binding.gke_key_binding
  # ]

  default_snat_status {
    disabled = false
  }

  gateway_api_config {
    channel = "CHANNEL_STANDARD"
  }

  logging_config {
    enable_components = [
      "APISERVER",
      "CONTROLLER_MANAGER",
      "KCP_HPA",
      "SCHEDULER",
      "SYSTEM_COMPONENTS",
      "WORKLOADS",
    ]
  }

  monitoring_config {
    enable_components = [
      "APISERVER",
      "CADVISOR",
      "CONTROLLER_MANAGER",
      "DAEMONSET",
      "DCGM",
      "DEPLOYMENT",
      "HPA",
      "JOBSET",
      "KUBELET",
      "POD",
      "SCHEDULER",
      "STATEFULSET",
      "STORAGE",
      "SYSTEM_COMPONENTS",
    ]

    advanced_datapath_observability_config {
      enable_metrics = true
      enable_relay   = true
    }

    managed_prometheus {
      enabled = true
    }
  }

  private_cluster_config {
    enable_private_nodes   = true
    master_ipv4_cidr_block = "172.16.0.64/28"
  }

  security_posture_config {
    mode               = "BASIC"
    vulnerability_mode = "VULNERABILITY_BASIC"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  lifecycle {
    ignore_changes = [node_config]
  }
}

# Main CPU node pool
resource "google_container_node_pool" "main" {
  name_prefix = "main-"
  cluster     = google_container_cluster.gke.id

  node_config {
    machine_type = "e2-standard-8"

    service_account = local.default_compute_service_account
    disk_type       = "pd-balanced"
    disk_size_gb    = 100

    gcfs_config {
      enabled = true
    }

    gvnic {
      enabled = true
    }

    shielded_instance_config {
      enable_secure_boot = true
    }
  }

  autoscaling {
    total_min_node_count = 0
    total_max_node_count = 3
  }

  lifecycle {
    create_before_destroy = true
  }
}

# GPU node pool with queued provisioning (requires Kueue)
resource "google_container_node_pool" "gpu-dws-8g-ultra" {
  provider    = google-beta
  name_prefix = "gpu-dws-8gu-"
  cluster     = google_container_cluster.gke.id

  max_pods_per_node = 32
  node_locations    = local.gpu_zones

  queued_provisioning {
    enabled = true
  }

  node_config {
    machine_type = "a3-ultragpu-8g"
    flex_start   = true

    service_account = local.default_compute_service_account
    disk_type       = "hyperdisk-balanced"
    disk_size_gb    = 100

    guest_accelerator {
      type = "nvidia-h200-141gb"
      gpu_driver_installation_config {
        gpu_driver_version = "LATEST"
      }
      count = 8
    }

    ephemeral_storage_local_ssd_config {
      local_ssd_count = 32
    }

    gcfs_config {
      enabled = true
    }

    gvnic {
      enabled = true
    }

    # fast_socket {
    #   enabled = true
    # }

     labels = {
    #   "cloud.google.com/gke-kdump-enabled" = "true"
        "kueue.x-k8s.io/queue-name" = "dws-local-queue"
        "skypilot.co/accelerator" = "H200"
     }

    reservation_affinity {
      consume_reservation_type = "NO_RESERVATION"
    }

    shielded_instance_config {
      enable_secure_boot = true
    }
  }

  autoscaling {
    total_min_node_count = 0
    # max_node_count = 16
    total_max_node_count = 128
    location_policy      = "ANY"
  }

  management {
    auto_repair  = false
    auto_upgrade = true
  }

  network_config {
    enable_private_nodes = true
  }

  lifecycle {
    create_before_destroy = true
  }
}

# GPU node pool without queued provisioning
resource "google_container_node_pool" "gpu-8g-ultra" {
  provider    = google-beta
  name_prefix = "gpu-8gu-"
  cluster     = google_container_cluster.gke.id

  max_pods_per_node = 32
  node_locations    = local.gpu_zones

  queued_provisioning {
    enabled = false
  }

  node_config {
    machine_type     = "a3-ultragpu-8g"
    flex_start       = true
    max_run_duration = "604800s"

    service_account = local.default_compute_service_account
    disk_type       = "hyperdisk-balanced"
    disk_size_gb    = 100

    guest_accelerator {
      type = "nvidia-h200-141gb"
      gpu_driver_installation_config {
        gpu_driver_version = "LATEST"
      }
      count = 8
    }

    ephemeral_storage_local_ssd_config {
      local_ssd_count = 32
    }

    gcfs_config {
      enabled = true
    }

    gvnic {
      enabled = true
    }

    # fast_socket {
    #   enabled = true
    # }

     labels = {
    #   "cloud.google.com/gke-kdump-enabled" = "true"
        "skypilot.co/accelerator" = "H200"
     }

    reservation_affinity {
      consume_reservation_type = "NO_RESERVATION"
    }

    shielded_instance_config {
      enable_secure_boot = true
    }
  }

  autoscaling {
    total_min_node_count = 0
    total_max_node_count = 128
    location_policy      = "ANY"
  }

  management {
    auto_repair  = false
    auto_upgrade = true
  }

  network_config {
    enable_private_nodes = true
  }

  lifecycle {
    create_before_destroy = true
  }
} 