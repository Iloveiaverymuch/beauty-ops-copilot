# Created WITH the service, before any traffic: a log-based metric never backfills (D2.4: 6 denies in logs, 5 in
# the metric, because it was created after the first deny).
resource "google_logging_metric" "governance_denies" {
  name        = "governance_denies"
  description = "Tool calls denied by the governance plugin"
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"copilot-api\" AND jsonPayload.event=\"tool_decision\" AND jsonPayload.decision=\"deny\""
}

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  display_name = "Copilot on-call"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

# A spike = attack wave, a prompt regression that makes the model try forbidden actions, or a broken policy.
resource "google_monitoring_alert_policy" "governance_denies_spike" {
  display_name = "copilot-api: governance denies spike"
  combiner     = "OR"

  conditions {
    display_name = "denies in 5 min > ${var.deny_alert_threshold}"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND metric.type = \"logging.googleapis.com/user/${google_logging_metric.governance_denies.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.deny_alert_threshold
      duration        = "0s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }
      trigger {
        count = 1
      }
    }
  }

  notification_channels = google_monitoring_notification_channel.email[*].id

  documentation {
    mime_type = "text/markdown"
    content   = "Governance denies above baseline. Check Logs Explorer `jsonPayload.event=\"tool_decision\" jsonPayload.decision=\"deny\"`: same reason + many sessions = attack wave or prompt regression; check the last deploy. Runbook: docs/RUNBOOK.md (D3.5)."
  }
}
