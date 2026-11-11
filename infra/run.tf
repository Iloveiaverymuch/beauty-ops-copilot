# Two private Cloud Run services. Defense in depth on the MCP server:
#   edge: only copilot-sa has run.invoker on m365-mcp (403 for anyone else)
#   app : token audience + caller email -> scopes (401 even for a project Owner)

locals {
  mcp_env = [
    { name = "GOOGLE_AUDIENCE", value = local.mcp_url },
    { name = "MCP_RESOURCE_URL", value = "${local.mcp_url}/mcp" },
    # same literal format as the gcloud deploy (spaces included) so the import shows no diff
    { name = "MCP_CALLER_SCOPES", value = "{\"${google_service_account.copilot.email}\": [\"Mail.Read\", \"Calendars.Read\", \"Files.Read.All\", \"Mail.ReadWrite\"]}" },
  ]

  copilot_env = concat([
    { name = "GOOGLE_GENAI_USE_VERTEXAI", value = "TRUE" },
    { name = "GOOGLE_CLOUD_PROJECT", value = var.project_id },
    { name = "GOOGLE_CLOUD_LOCATION", value = var.vertex_location },
    { name = "MODEL", value = "gemini-3.8-flash" },
    { name = "M365_MCP_URL", value = "${local.mcp_url}/mcp" },
    { name = "M365_MCP_AUTH", value = "google" },
    { name = "M365_MCP_AUDIENCE", value = local.mcp_url },
    { name = "M365_PROMPT", value = "hardened_v2" },
    { name = "M365_CONFIRM_WRITES", value = "1" },
    { name = "GOVERNANCE", value = "enforce" },
    { name = "ROUTER_MODE", value = "agenttool" },
    { name = "THINKING_LEVEL_ROUTER", value = "LOW" }, # per-agent thinking by risk (D2.2)
    { name = "THINKING_LEVEL_CATALOG", value = "LOW" },
    { name = "OTEL_SERVICE_NAME", value = "copilot-api" },
    { name = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", value = "NO_CONTENT" }, # no mail bodies in traces
    { name = "DEPLOY_ENV", value = "cloudrun" },
    ], var.dev_ui ? [
    # ADK origin check vs the local proxy's Origin (blank dev UI otherwise, D2.3). Dev UI only.
    { name = "ALLOW_ORIGINS", value = "http://localhost:8000,http://127.0.0.1:8000" },
    ] : [{ name = "ADK_WEB", value = "0" }], [
    { name = "HOME", value = "/tmp" },                    # image runs as nobody (home /nonexistent); ADK writes ~/.adk
    { name = "OTEL_TRACES_SAMPLER", value = "always_on" }, # don't inherit Cloud Run's sampled=0 (traces silently lost)
  ])
}

resource "google_cloud_run_v2_service" "mcp" {
  name                = "m365-mcp"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL" # reachable URL, but IAM-protected (no allUsers invoker)
  deletion_protection = var.deletion_protection

  template {
    service_account = google_service_account.mcp.email
    scaling {
      max_instance_count = 2
    }
    containers {
      image = "${local.registry}/m365-mcp:${var.initial_image_tag}"
      resources {
        limits            = { cpu = "1000m", memory = "512Mi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = local.mcp_env
        content {
          name  = env.value.name
          value = env.value.value
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }
  depends_on = [google_project_service.apis, google_project_iam_member.mcp]
}

resource "google_cloud_run_v2_service" "copilot" {
  name                = "copilot-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.deletion_protection

  template {
    service_account = google_service_account.copilot.email
    timeout         = "600s" # agent turns + SSE: 100 s+ tails measured
    scaling {
      max_instance_count = 1 # in-memory sessions: an approval must resume on the same instance (D3.3 lifts this)
    }
    containers {
      image = "${local.registry}/copilot-api:${var.initial_image_tag}"
      resources {
        limits            = { cpu = "1", memory = "1Gi" }
        cpu_idle          = false # --no-cpu-throttling: BatchSpanProcessor can flush outside requests (cost: D3.4)
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = local.copilot_env
        content {
          name  = env.value.name
          value = env.value.value
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }
  depends_on = [google_project_service.apis, google_project_iam_member.copilot, google_cloud_run_v2_service.mcp]
}

# Service-level (not project-wide) invoker: only the copilot identity can call the MCP server.
resource "google_cloud_run_v2_service_iam_member" "copilot_invokes_mcp" {
  name     = google_cloud_run_v2_service.mcp.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.copilot.email}"
}
