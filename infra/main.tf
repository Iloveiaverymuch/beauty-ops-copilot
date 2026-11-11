data "google_project" "this" {
  project_id = var.project_id
}

locals {
  project_number = data.google_project.this.number
  registry       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repo}"
  # Deterministic Cloud Run URL (same on both sides: token audience, GOOGLE_AUDIENCE, M365_MCP_URL). Not the
  # resource's `uri`, which is the legacy hash URL: an audience mismatch = 401 (DEPLOY.md troubleshooting).
  mcp_url = "https://m365-mcp-${local.project_number}.${var.region}.run.app"

  apis = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudresourcemanager.googleapis.com", # ADK otel_to_cloud resolves the project number
    "aiplatform.googleapis.com",
    "telemetry.googleapis.com", # OTLP traces -> Cloud Trace
  ])

  copilot_roles = toset([
    "roles/aiplatform.user",
    "roles/cloudtrace.agent",
    "roles/monitoring.metricWriter",
    "roles/logging.logWriter",
  ])
  mcp_roles = toset(["roles/logging.logWriter"])

  # Cloud Build runs as the default compute SA in this project (D2.3). D3.2 replaces it with a dedicated build SA.
  build_sa    = "${local.project_number}-compute@developer.gserviceaccount.com"
  build_roles = toset(["roles/artifactregistry.writer", "roles/logging.logWriter", "roles/storage.objectViewer"])
}

resource "google_project_service" "apis" {
  for_each           = local.apis
  service            = each.value
  disable_on_destroy = false # never switch off an API other things may use
}

resource "google_artifact_registry_repository" "agents" {
  repository_id = var.repo
  location      = var.region
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# Least privilege: one identity per service, never the default compute SA for the app.
resource "google_service_account" "copilot" {
  account_id   = "copilot-sa"
  display_name = "copilot-api runtime"
}

resource "google_service_account" "mcp" {
  account_id   = "mcp-sa"
  display_name = "m365-mcp runtime"
}

# _member (additive), not _binding/_policy (authoritative): never remove grants someone else made in the project.
resource "google_project_iam_member" "copilot" {
  for_each = local.copilot_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.copilot.email}"
}

resource "google_project_iam_member" "mcp" {
  for_each = local.mcp_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.mcp.email}"
}

resource "google_project_iam_member" "build" {
  for_each = local.build_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${local.build_sa}"
}
