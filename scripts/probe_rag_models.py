"""R1 probe: which regions serve the embedding model, and does the Vertex AI ranking API (cross-encoder) answer?
Same lesson as H0: probe with real calls, don't trust catalog pages.

    uv run --env-file copilot/.env python scripts/probe_rag_models.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import google.auth
from google.auth.transport.requests import AuthorizedSession
from google import genai
from google.genai import types

from advisor import config

print(f"project={config.PROJECT} model={config.EMBED_MODEL} dim={config.EMBED_DIM}")
ok_regions = []
for loc in ["europe-west1", "europe-west4", "eu", "global"]:
    try:
        t = time.perf_counter()
        c = genai.Client(vertexai=True, project=config.PROJECT, location=loc)  # keep a reference: Client closes its HTTP pool in __del__
        r = c.models.embed_content(
            model=config.EMBED_MODEL, contents="sérum niacinamide peau grasse",
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY", output_dimensionality=config.EMBED_DIM))
        ms = (time.perf_counter() - t) * 1000
        c.close()
        print(f"EMBED_OK  {loc:13s} dim={len(r.embeddings[0].values)} {ms:.0f} ms")
        ok_regions.append(loc)
    except Exception as e:  # noqa: BLE001
        print(f"EMBED_ERR {loc:13s} {type(e).__name__}: {str(e)[:120]}")

creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
s = AuthorizedSession(creds)
url = (f"https://discoveryengine.googleapis.com/v1/projects/{config.PROJECT}/locations/{config.RANK_LOCATION}"
       f"/rankingConfigs/default_ranking_config:rank")
body = {"model": config.RANK_MODEL, "query": "Peut-on utiliser le rétinol avec la vitamine C ?", "topN": 3,
        "records": [{"id": "a", "content": "La vitamine C s'applique le matin, le rétinol le soir."},
                    {"id": "b", "content": "Livraison offerte dès 35 euros d'achat."},
                    {"id": "c", "content": "Le rétinol rend la peau sensible au soleil."}]}
t = time.perf_counter()
r = s.post(url, json=body, headers={"x-goog-user-project": config.PROJECT})
ms = (time.perf_counter() - t) * 1000
if r.ok:
    print(f"RANK_OK   {config.RANK_LOCATION} {ms:.0f} ms " + " ".join(f"{x['id']}={x['score']:.2f}" for x in r.json()["records"]))
else:
    print(f"RANK_ERR  {config.RANK_LOCATION} {r.status_code}: {r.text[:200]}")
print(f"=> set EMBED_LOCATION={ok_regions[0] if ok_regions else '??'} in copilot/.env")
