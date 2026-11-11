# D3.1: everything from deploy/DEPLOY.md "Manual steps log" as code.
# Ownership split: Terraform owns the SHAPE of the platform (APIs, identities, IAM, services, limits, env, metrics,
# alerts). CI (D3.2) owns the IMAGE: `gcloud run deploy --image` changes only that, and Terraform ignores it.
terraform {
  required_version = ">= 1.7" # for_each in import blocks

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.20, < 8.0"
    }
  }

  # Remote state: one source of truth, locking, versioned history (bucket created once by hand, see README:
  # the bucket that holds the state can't be created by the config that stores its state in it).
  backend "gcs" {
    bucket = "rk-adk-poc-1104-tfstate"
    prefix = "beauty-ops-copilot"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
