variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "europe-west1"
}

variable "repo" {
  type    = string
  default = "agents"
}

variable "vertex_location" {
  description = "Gemini endpoint: eu = residency default; global = explicit decision (latency/availability)."
  type        = string
  default     = "eu"
}

variable "initial_image_tag" {
  description = "Only used when Terraform CREATES a service. Afterwards CI deploys images and Terraform ignores them."
  type        = string
  default     = "latest"
}

variable "dev_ui" {
  description = "ADK dev UI on the copilot (demo behind IAM only). Prod: false."
  type        = bool
  default     = true
}

variable "deny_alert_threshold" {
  description = "Alert when governance denies in 5 min exceed this (baseline: 0-2 in normal use)."
  type        = number
  default     = 3
}

variable "alert_email" {
  description = "Optional email for alerts. Empty = policy exists, no notification channel."
  type        = string
  default     = ""
}

variable "deletion_protection" {
  description = "Cloud Run deletion protection. true = `terraform destroy` refuses to delete the services."
  type        = bool
  default     = true
}
