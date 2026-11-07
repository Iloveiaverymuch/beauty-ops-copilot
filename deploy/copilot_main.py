"""Cloud Run entrypoint for the copilot. Same App as `adk web`, served by ADK's FastAPI app.

- otel_to_cloud: ADK builds the TracerProvider with the GCP resource (gcp.project_id) -> Cloud Trace works (D2.2).
- web=ADK_WEB: dev UI on/off (keep on for the demo behind IAM; off in prod).
- sessions: in-memory for D2.3 (max-instances=1 so approval resumes hit the same instance); persistent in D3.3.
"""
import os
from pathlib import Path

import uvicorn
from google.adk.cli.fast_api import get_fast_api_app

app = get_fast_api_app(
    agents_dir=str(Path(__file__).resolve().parent),
    web=os.getenv("ADK_WEB", "1") == "1",
    otel_to_cloud=os.getenv("OTEL_TO_CLOUD", "1") == "1",
    session_service_uri=os.getenv("SESSION_SERVICE_URI") or None,
    use_local_storage=False,  # read-only container FS; never write sessions/artifacts to disk
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
