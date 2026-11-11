output "mcp_url" {
  value = local.mcp_url
}

output "copilot_service" {
  value = google_cloud_run_v2_service.copilot.name
}

output "proxy_command" {
  value = "gcloud run services proxy ${google_cloud_run_v2_service.copilot.name} --region=${var.region} --port=8000"
}

output "copilot_sa" {
  value = google_service_account.copilot.email
}
