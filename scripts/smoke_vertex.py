"""H0 smoke test: proves ADC + Vertex AI + Gemini work from this machine.
Run: uv run --env-file copilot/.env python scripts/smoke_vertex.py
"""
import os
import sys

import google.auth
from google import genai

creds, adc_project = google.auth.default()
project = os.environ.get("GOOGLE_CLOUD_PROJECT")
location = os.environ.get("GOOGLE_CLOUD_LOCATION", "europe-west1")
model = os.environ.get("MODEL", "gemini-3.8-flash")
print(f"ADC ok ({type(creds).__name__}), adc quota project={adc_project}, using project={project}, location={location}")
if not project:
    sys.exit("GOOGLE_CLOUD_PROJECT missing: fill copilot/.env")

client = genai.Client(vertexai=True, project=project, location=location)
r = client.models.generate_content(model=model, contents="Reply with exactly: VERTEX_OK")
print(f"{model}: {r.text.strip()}  | tokens in/out = {r.usage_metadata.prompt_token_count}/{r.usage_metadata.candidates_token_count}")
