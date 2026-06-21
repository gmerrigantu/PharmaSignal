# PharmaSignal Serving API — Deployment Guide (Option B)

This is the **API layer** that connects the Vercel-hosted Next.js frontend to the S3
data backend. The browser never talks to S3/Athena directly (no AWS credentials can be
safely exposed client-side). Instead:

```
Vercel (Next.js, server-side fetch + ISR)
        │  HTTPS, CORS
        ▼
API Gateway (HTTP API, $default stage)
        │  AWS_PROXY (payload v2)
        ▼
Lambda  (container image: FastAPI + Mangum)
        │  IAM role  →  read-only S3
        ▼
s3://<bucket>/gold/*.parquet   (read with DuckDB/pandas, NOT Athena)
```

**Why DuckDB-over-S3, not Athena at request time:** the gold tables total ~0.36 MB.
Reading the Parquet directly is faster and cheaper than per-request Athena queries.
Athena/Glue remain available for ad-hoc analytics (`infrastructure/aws_deploy.py query`).

**Why a container image, not a zip:** pandas + pyarrow + duckdb exceed Lambda's 250 MB
unzipped zip limit. The container (10 GB limit) ships them cleanly and reuses the
existing serving code unchanged.

---

## Endpoints

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness + visible tables/row counts + data source |
| `GET /dashboard/summary` | **The payload the Next.js app fetches** — all six dashboard tables (`DashboardData`) |
| `GET /signals?drug=&event=&drug_class=&flagged_only=&min_reports=&limit=` | Filterable disproportionality scores (sorted by ROR) |
| `GET /emerging?priority=&limit=` | Emerging signals (sorted by composite priority) |
| `GET /drugs/{drug}` | One-drug profile: signals + emerging + NHANES + evidence (404 if unknown) |
| `GET /nhanes` | NHANES population context |
| `GET /evidence?drug=&event=` | PubMed evidence rows |
| `GET /interactions?drug=` · `GET /subgroups?drug=` | Advanced marts (empty on the demo dataset) |

Read-only. Responses are JSON-safe (NaN/Inf → `null`). CORS is restricted to the
origins in `PHARMASIGNAL_CORS_ORIGINS`.

---

## Run locally (no AWS, no Docker)

```bash
make install-api                 # fastapi, mangum, uvicorn, pandas, duckdb, …
make api-local                   # uvicorn on :8000, reads PHARMASIGNAL_DATA_ROOT from .env
#   (unset PHARMASIGNAL_DATA_ROOT → serves the bundled demo gold dataset)
curl localhost:8000/health
curl localhost:8000/dashboard/summary | jq 'keys'
```

Point the frontend at it: in `frontend/.env.local`
`NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL=http://localhost:8000`.

---

## Deploy to AWS

**Prerequisites:** Docker running; AWS creds in the env chain
(`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION`) with permission
to manage ECR, Lambda, IAM, and API Gateway v2.

```bash
# Builds+pushes the image to ECR, creates the IAM role (S3 read-only), the Lambda,
# and the HTTP API with CORS locked to your Vercel domain.
python infrastructure/api_deploy.py deploy \
  --bucket pharmasignal-data-<unique-suffix> \
  --cors-origins https://your-app.vercel.app \
  --region us-east-1
# or:  make api-deploy BUCKET=pharmasignal-data-<suffix> CORS=https://your-app.vercel.app
```

The command prints the **API base URL**, e.g.
`https://abcd1234.execute-api.us-east-1.amazonaws.com` (HTTP APIs use the `$default`
stage — there is **no** `/prod` segment). Re-print it any time with:

```bash
python infrastructure/api_deploy.py url --region us-east-1
```

Verify:

```bash
curl https://<api-id>.execute-api.us-east-1.amazonaws.com/health
```

### Redeploying after a code or data change
Re-run `deploy` — it rebuilds/pushes the image and calls `update-function-code`. Gold
data changes need no redeploy (the Lambda reads current S3 on a ≤5-min cache; tune with
the `PHARMASIGNAL_CACHE_TTL` Lambda env var).

### Tear down
```bash
python infrastructure/api_deploy.py destroy --region us-east-1            # API + Lambda + role
python infrastructure/api_deploy.py destroy --region us-east-1 --delete-ecr  # also the image repo
```

---

## Wire up Vercel

1. Vercel project root = `frontend/`.
2. Project → Settings → Environment Variables, add:
   `NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL = https://<api-id>.execute-api.us-east-1.amazonaws.com`
3. Redeploy the frontend so the build inlines the value.

The frontend fetches **server-side** with `next: { revalidate: 300 }`, so pages are
statically/ISR-cached and AWS is hit at most once per 5 minutes per route. If the API
is unreachable, the app falls back to the bundled demo payload and shows a warning on
the Pipeline Health card.

`NEXT_PUBLIC_*` is intentionally public (it's just a URL). **Never** put `AWS_*`,
`OPENFDA_API_KEY`, or other secrets in a `NEXT_PUBLIC_*` var.

---

## Security / cost notes

- **No static AWS keys anywhere in the request path.** The Lambda uses an IAM role
  scoped to `s3:GetObject`/`s3:ListBucket` on the one bucket — read-only, no writes,
  no Athena, no other services.
- **CORS** is restricted to the origin(s) you pass to `--cors-origins`. Use `*` only
  for local testing.
- **Cost:** Lambda + HTTP API are pay-per-request with a generous free tier; with ISR
  caching the call volume is tiny. No always-on compute. Effectively ~$0/month at
  typical traffic, on top of the ~$0.01/month S3 storage.
