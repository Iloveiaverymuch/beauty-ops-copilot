# infra/ (D3.1): the platform as code

> **Status: ON HOLD (2026-11-11), written but NOT applied.** (`plan` is read-only and safe; `apply` is the step not done.)
> Resume order: bootstrap bucket -> init -> plan (fix import diffs until only intended changes) -> apply -> plan = "No changes".
> Expected intended diffs: copilot loses `SERVICE_VERSION` env; alert policy created.

Terraform owns the **shape** (APIs, identities, IAM, Cloud Run services, limits, env, metric, alert).
CI owns the **image** (`gcloud run deploy --image`; Terraform ignores it). Never edit a service by hand again:
change the .tf, `plan`, `apply`.

| File | What |
|---|---|
| `versions.tf` | Terraform + provider versions, GCS remote state |
| `main.tf` | APIs, Artifact Registry, 2 runtime SAs, project roles (additive `_member`), build SA roles |
| `run.tf` | `m365-mcp` + `copilot-api` (env, limits, sampler, HOME, CPU), service-level `run.invoker` |
| `observability.tf` | `governance_denies` log metric, alert policy (+ optional email channel) |
| `imports.tf` | one-time adoption of the D2.3 hand-made resources |

## Once: state bucket (bootstrap)
The bucket holding the state can't be created by the config that stores its state in it.
```bash
gcloud storage buckets create gs://rk-adk-poc-1104-tfstate --project=rk-adk-poc-1104 --location=europe-west1 \
  --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://rk-adk-poc-1104-tfstate --versioning   # every state version recoverable
```

## Loop
```bash
cd infra
terraform init          # downloads provider, connects the GCS backend
terraform fmt && terraform validate
terraform plan -out=tfplan     # read EVERY line: imports, updates in place, and never an unexpected destroy
terraform apply tfplan
terraform plan                 # must say: No changes. (= code and cloud agree)
```
