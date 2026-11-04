"""Find which Gemini model IDs your project can call, per location.
Run: uv run --env-file copilot/.env python scripts/probe_models.py [model_id ...]
Prints a matrix; pick the first OK cell that satisfies data residency (EU regional > global).
"""
import os
import sys

from google import genai
from google.genai import errors

project = os.environ["GOOGLE_CLOUD_PROJECT"]
_candidates = sys.argv[1:] or [
    os.environ.get("MODEL", ""), "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
    "gemini-3.5-flash", "gemini-3.8-flash-lite", "gemini-3.1-flash-lite", "gemini-3-flash-preview",
]
models = [m for m in dict.fromkeys(_candidates) if m]  # dedupe, keep order, drop empty
locations = ["eu", "europe-west1", "europe-west4", "global"]

# 1) what the catalog says (base publisher models visible from 'global')
try:
    c = genai.Client(vertexai=True, project=project, location="global")
    names = sorted({m.name.split("/")[-1] for m in c.models.list(config={"query_base": True})
                    if "gemini" in (m.name or "") and "flash" in (m.name or "")})
    print("catalog (global, flash):", ", ".join(names[:20]) or "(empty)")
except Exception as e:  # listing is best-effort
    print("catalog listing failed:", type(e).__name__, str(e)[:120])

# 2) real calls: the only proof that counts
print(f"\n{'model':28}" + "".join(f"{l:15}" for l in locations))
for m in models:
    row = f"{m:28}"
    for loc in locations:
        try:
            cl = genai.Client(vertexai=True, project=project, location=loc)
            cl.models.generate_content(model=m, contents="ok", config={"max_output_tokens": 5})
            row += f"{'OK':15}"
        except errors.APIError as e:
            row += f"{str(e.code):15}"
        except Exception as e:
            row += f"{type(e).__name__[:13]:15}"
    print(row)
