# D2.3 first deploy: 2 Cloud Run services, manual gcloud (every step here = a Terraform resource in D3.1)

```
you --(gcloud run services proxy, your identity)--> copilot-api  [private, SA copilot-sa]
                                                       |  Google ID token (aud = m365-mcp URL), header_provider
                                                       v
                                                    m365-mcp     [private, SA mcp-sa]
  edge: Cloud Run IAM (roles/run.invoker)  ->  app: verify token + caller email -> per-caller scopes
```

Run block by block from the repo root. Note what you do: it becomes the D3.1 checklist.

## 0. Variables
```bash
export PROJECT=rk-adk-poc-1104 REGION=europe-west1 REPO=agents TAG=$(git rev-parse --short HEAD)
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
export MCP_URL=https://m365-mcp-$PROJECT_NUMBER.$REGION.run.app        # Cloud Run URLs are deterministic
export COPILOT_SA=copilot-sa@$PROJECT.iam.gserviceaccount.com MCP_SA=mcp-sa@$PROJECT.iam.gserviceaccount.com
export VERTEX_LOCATION=eu      # residency; switch to global if the eu tail hurts the demo (record the decision)
gcloud config set project $PROJECT
gcloud config set run/region $REGION    # stops every `gcloud run` command from prompting for a region
echo "project=$PROJECT number=$PROJECT_NUMBER tag=$TAG mcp=$MCP_URL"   # all must be non-empty
```
`export` lives only in the current terminal: in any NEW terminal, re-run this block first (empty variables were the
cause of the H0 `.env` and region-prompt surprises).

## 1. APIs + Artifact Registry
```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  monitoring.googleapis.com iamcredentials.googleapis.com cloudresourcemanager.googleapis.com
# cloudresourcemanager: ADK's otel_to_cloud resolves the project NUMBER for span labels (agent URN); without it a
# noisy PERMISSION/SERVICE_DISABLED dump at startup (cosmetic, traces still flow).
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION
```

## 2. Service accounts (least privilege; never the default compute SA for the app)
```bash
gcloud iam service-accounts create copilot-sa --display-name="copilot-api runtime"
gcloud iam service-accounts create mcp-sa --display-name="m365-mcp runtime"
for r in roles/aiplatform.user roles/cloudtrace.agent roles/monitoring.metricWriter roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$COPILOT_SA --role=$r --condition=None -q >/dev/null; done
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$MCP_SA --role=roles/logging.logWriter --condition=None -q >/dev/null
# Cloud Build runs as the default compute SA in new projects: let it push images and write build logs
export BUILD_SA=$PROJECT_NUMBER-compute@developer.gserviceaccount.com
for r in roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer; do
  gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$BUILD_SA --role=$r --condition=None -q >/dev/null; done
```

## 3. Build both images (Cloud Build, no local Docker needed)
```bash
gcloud builds submit --config deploy/cloudbuild.yaml --substitutions=_REGION=$REGION,_REPO=$REPO,_TAG=$TAG
```

## 4. Deploy the MCP server (private)
`^|^` changes gcloud's env-var separator so the JSON value can contain commas.
```bash
gcloud run deploy m365-mcp --image=$REGION-docker.pkg.dev/$PROJECT/$REPO/m365-mcp:$TAG --region=$REGION \
  --service-account=$MCP_SA --no-allow-unauthenticated --max-instances=2 --memory=512Mi \
  --set-env-vars="^|^GOOGLE_AUDIENCE=$MCP_URL|MCP_RESOURCE_URL=$MCP_URL/mcp|MCP_CALLER_SCOPES={\"$COPILOT_SA\": [\"Mail.Read\", \"Calendars.Read\", \"Files.Read.All\", \"Mail.ReadWrite\"]}"
# Cloud Run gives each service 2 URLs (deterministic https://SVC-PROJECT_NUMBER.REGION.run.app + legacy hash URL);
# both work. We use the deterministic one everywhere: token audience, GOOGLE_AUDIENCE and M365_MCP_URL must match.
# only the copilot identity may call it (service-level binding, not project-wide)
gcloud run services add-iam-policy-binding m365-mcp --region=$REGION \
  --member=serviceAccount:$COPILOT_SA --role=roles/run.invoker
```

## 5. Deploy the copilot (private)
`max-instances=1`: sessions are in memory, so an approval must resume on the same instance (fixed in D3.3).
`timeout=600`: agent turns plus SSE can be long (we've measured 100 s+ tails).
```bash
gcloud run deploy copilot-api --image=$REGION-docker.pkg.dev/$PROJECT/$REPO/copilot-api:$TAG --region=$REGION \
  --service-account=$COPILOT_SA --no-allow-unauthenticated --max-instances=1 --memory=1Gi --cpu=1 --timeout=600 \
  --set-env-vars="^|^GOOGLE_GENAI_USE_VERTEXAI=TRUE|GOOGLE_CLOUD_PROJECT=$PROJECT|GOOGLE_CLOUD_LOCATION=$VERTEX_LOCATION|MODEL=gemini-3.8-flash|M365_MCP_URL=$MCP_URL/mcp|M365_MCP_AUTH=google|M365_MCP_AUDIENCE=$MCP_URL|M365_PROMPT=hardened_v2|M365_CONFIRM_WRITES=1|GOVERNANCE=enforce|ROUTER_MODE=agenttool|THINKING_LEVEL_ROUTER=LOW|THINKING_LEVEL_CATALOG=LOW|OTEL_SERVICE_NAME=copilot-api|OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT|DEPLOY_ENV=cloudrun|SERVICE_VERSION=$TAG|ALLOW_ORIGINS=http://localhost:8000,http://127.0.0.1:8000"
```
`ALLOW_ORIGINS`: ADK's origin check (CSRF defence) otherwise 403s the dev UI's JS through the proxy (blank page).
Only for the IAM-private demo with the dev UI on; in prod set `ADK_WEB=0` and drop it.

## 6. Use it (the proxy adds your identity token; you're Owner so run.invoker is implied)
```bash
gcloud run services proxy copilot-api --region=$REGION --port=8000      # then open http://localhost:8000
# first use installs the cloud-run-proxy component; if it says "binary not installed", just run it again
# open it in Chrome/Safari (an editor's embedded preview browser can't run the dev UI); hard-refresh if you ran
# a local `adk web` on the same port before (cached assets)
```
Send the same requests as in D2.2 (mixed draft with approval, then the gmail draft that gets blocked).

## 7. Prove the S2S boundary (defense in depth, 2 layers)
```bash
curl -s -o /dev/null -w "no token        -> %{http_code}\n" -X POST $MCP_URL/mcp                     # edge: 403
curl -s -o /dev/null -w "your user token -> %{http_code}\n" -X POST $MCP_URL/mcp \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "content-type: application/json" -H "accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# Expected (observed 2026-11-08): 403 without token (edge), 401 with your user token: you pass the EDGE
# (Owner => run.invoker) but the APP rejects you (your email isn't in MCP_CALLER_SCOPES).
```

## 8. Audit log -> Cloud Logging -> metric
```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="copilot-api" AND jsonPayload.event="tool_decision"' \
  --limit=5 --format='value(severity,jsonPayload.agent,jsonPayload.tool,jsonPayload.decision,jsonPayload.reason)'
gcloud logging metrics create governance_denies \
  --description="Tool calls denied by the governance plugin" \
  --log-filter='resource.type="cloud_run_revision" AND resource.labels.service_name="copilot-api" AND jsonPayload.event="tool_decision" AND jsonPayload.decision="deny"'
```
In the console, open a denied entry and click the trace link (`logging.googleapis.com/trace`): it takes you to the span.

## Redeploy after a code change (no infra change)
`services update` swaps the image and keeps every other setting (env, SA, limits).
```bash
git add -A && git commit -m "..." && export TAG=$(git rev-parse --short HEAD)
gcloud builds submit --config deploy/cloudbuild.yaml --substitutions=_REGION=$REGION,_REPO=$REPO,_TAG=$TAG
gcloud run services update m365-mcp    --image=$REGION-docker.pkg.dev/$PROJECT/$REPO/m365-mcp:$TAG
gcloud run services update copilot-api --image=$REGION-docker.pkg.dev/$PROJECT/$REPO/copilot-api:$TAG \
  --update-env-vars=SERVICE_VERSION=$TAG
# config-only change, no rebuild, e.g. the residency/availability switch (record it as a decision):
# gcloud run services update copilot-api --update-env-vars=GOOGLE_CLOUD_LOCATION=global
```

## Troubleshooting (all hit on the first deploy, 2026-11-08)
| Symptom | Cause | Fix |
|---|---|---|
| Dev UI blank; logs: JS chunks `403`, CSS `200` | ADK origin-check middleware: browser `Origin: localhost:8000` vs `Host: ...run.app` through the proxy (only CORS-mode JS module requests send `Origin`) | `ALLOW_ORIGINS=http://localhost:8000,http://127.0.0.1:8000` (dev UI only; prod `ADK_WEB=0`) |
| `POST /run_sse 500`, `PermissionError ... agent.py` | Source files were mode 600; container runs as `nobody` | Dockerfiles `chmod -R a+rX` (readable, not writable by the process); normalize repo file modes |
| Startup dump `Cloud Resource Manager API has not been used` | ADK telemetry resolves the project number for span labels | Enable `cloudresourcemanager.googleapis.com` (block 1) |
| `cloud-run-proxy binary not installed` | Component installed during the same command | Run the proxy command again |
| `gcloud` asks for a region / variables empty | New terminal, block 0 not re-run | Re-run block 0 |
| Message sent, no answer, no error | Vertex `eu` latency tail (seen up to 417 s) | Wait, or switch `GOOGLE_CLOUD_LOCATION=global` (explicit decision) |
| MCP `401` in m365-mcp logs | Token audience != `GOOGLE_AUDIENCE`, or caller email not in `MCP_CALLER_SCOPES` | Same deterministic URL on both sides; check the SA email |

## 9. Cost and teardown
Both services scale to zero (min-instances=0): no idle cost. Delete everything:
```bash
gcloud run services delete copilot-api m365-mcp --region=$REGION -q
gcloud artifacts repositories delete $REPO --location=$REGION -q
```

## Manual steps log (input for D3.1 Terraform)
APIs (6, incl. cloudresourcemanager) · AR repo · 2 SAs · 4 project roles copilot-sa · 1 project role mcp-sa ·
3 roles build SA · 2 Cloud Run services (image, env incl. ALLOW_ORIGINS, SA, limits: copilot max 1 / 1Gi / 600 s,
mcp max 2 / 512Mi, ingress private) · 1 service-level run.invoker binding · 1 log-based metric (governance_denies).
Not yet: Secret Manager (no secrets so far: S2S uses ID tokens, no API keys), alert policy on governance_denies,
persistent sessions (D3.3).
