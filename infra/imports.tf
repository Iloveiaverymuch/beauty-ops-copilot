# One-time adoption of what D2.3 created by hand. After a clean `terraform apply` these blocks are no-ops; delete
# them then (or keep as documentation). Import = bring an existing object under management WITHOUT recreating it.
locals {
  pid = var.project_id
}

import {
  for_each = local.apis
  to       = google_project_service.apis[each.value]
  id       = "${local.pid}/${each.value}"
}

import {
  to = google_artifact_registry_repository.agents
  id = "projects/${local.pid}/locations/${var.region}/repositories/${var.repo}"
}

import {
  to = google_service_account.copilot
  id = "projects/${local.pid}/serviceAccounts/copilot-sa@${local.pid}.iam.gserviceaccount.com"
}

import {
  to = google_service_account.mcp
  id = "projects/${local.pid}/serviceAccounts/mcp-sa@${local.pid}.iam.gserviceaccount.com"
}

import {
  for_each = local.copilot_roles
  to       = google_project_iam_member.copilot[each.value]
  id       = "${local.pid} ${each.value} serviceAccount:copilot-sa@${local.pid}.iam.gserviceaccount.com"
}

import {
  for_each = local.mcp_roles
  to       = google_project_iam_member.mcp[each.value]
  id       = "${local.pid} ${each.value} serviceAccount:mcp-sa@${local.pid}.iam.gserviceaccount.com"
}

import {
  for_each = local.build_roles
  to       = google_project_iam_member.build[each.value]
  id       = "${local.pid} ${each.value} serviceAccount:${local.build_sa}"
}

import {
  to = google_cloud_run_v2_service.mcp
  id = "projects/${local.pid}/locations/${var.region}/services/m365-mcp"
}

import {
  to = google_cloud_run_v2_service.copilot
  id = "projects/${local.pid}/locations/${var.region}/services/copilot-api"
}

import {
  to = google_cloud_run_v2_service_iam_member.copilot_invokes_mcp
  id = "projects/${local.pid}/locations/${var.region}/services/m365-mcp roles/run.invoker serviceAccount:copilot-sa@${local.pid}.iam.gserviceaccount.com"
}

import {
  to = google_logging_metric.governance_denies
  id = "governance_denies"
}
# Not imported (new in D3.1): alert policy, notification channel.
