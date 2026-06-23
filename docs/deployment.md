# PharmaSignal — Deployment Guide

This covers all deployment paths: local development, AWS full-stack (current live
deployment), API layer, EMR Serverless backfill, and the Next.js frontend on Vercel.

> **Security rules:** never commit credentials or API keys; use environment variables /
> secret stores; the public dashboard must be read-only; use least-privilege IAM.

---

## Prerequisites & secrets

| Secret | Where to get it | How PharmaSignal reads it |
|---|---|---|
| `OPENFDA_API_KEY` | https://open.fda.gov/apis/authentication/ (free) | env var — optional, raises rate limit to 240/min |
| `NCBI_API_KEY` | https://www.ncbi.nlm.nih.gov/account/ → API Key Management | env var — optional, raises NCBI to 10 req/s |
| `NCBI_EMAIL` | your email (NCBI etiquette) | env var |
| AWS creds | IAM user / OIDC role | standard AWS SDK chain |

```bash
cp .env.example .env    # then edit values — never commit .env
```

---

## Option A — Local pipeline + S3 + Streamlit Community Cloud

**Cost: ≈ $0–1/month.** No always-on compute.

```bash
make install
make pipeline       # openFDA -> data/gold/*.parquet
make pipeline-full  # + labels + subgroups + interactions + nhanes + pubmed + enrich
```

Publish gold to S3:
```bash
aws s3 mb s3://pharmasignal-data-<unique-suffix>
aws s3api put-public-access-block --bucket pharmasignal-data-<unique-suffix> \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3 sync data/gold "s3://pharmasignal-data-<unique-suffix>/gold/"
```

Deploy the Streamlit dashboard to [Streamlit Community Cloud](https://share.streamlit.io):
- Push the repo to GitHub.
- New app → pick repo → **main file path = `dashboard/app.py`**.
- Advanced settings → Secrets: add `OPENFDA_API_KEY`, etc.
- To read live S3 data: set `PHARMASIGNAL_DATA_ROOT=s3://pharmasignal-data-<suffix>` in
  the app's environment.

---

## Option B — AWS full stack (current live deployment)

**Cost: ≈ $1–3/month.** Provision with the boto3 deploy script (no Terraform binary needed).

### Live AWS resources (region us-east-1)

| Resource | Name | Notes |
|---|---|---|
| S3 lakehouse bucket | `pharmasignal-data-<unique-suffix>` | Public access blocked, versioned, AES256; lifecycle on `athena-results/` + `tmp/` |
| Glue catalog database | `pharmasignal` | 11 external tables over S3 gold Parquet |
| Athena workgroup | `pharmasignal` | 1 GiB/query bytes-scanned guardrail; results → `s3://…/athena-results/` (auto-expire 14 days) |
| ECR repository | `pharmasignal-api` | FastAPI container image |
| Lambda function | `pharmasignal-api` | Container image; IAM role scoped to read-only S3 |
| HTTP API Gateway | `pharmasignal-api` | `$default` stage; CORS locked to Vercel origin |

### Gold tables registered in Glue / Athena (11)

`signal_scores` (999 rows), `drug_event_counts`, `emerging_signals` (50),
`drug_label_flags` (999), `subgroup_signals` (138, age/sex strata),
`interaction_signals` (1820, co-reported drug pairs), `nhanes_population_context` (9),
`pubmed_evidence`, `pubmed_support_summary`, `pipeline_health`, `data_quality_checks`.

### Live data produced

- **openFDA:** 10 drugs, **999 drug-event pairs**, **562 disproportionality-flagged**,
  universe = 7,084,818 reports (2021–2025 window).
- **Notable real signal:** `semaglutide → OPTIC ISCHAEMIC NEUROPATHY` (123 reports,
  ~99% serious) — consistent with the 2024 NAION signal — corroborated by a retrieved
  PubMed citation. Correctly reads **novel** in `drug_label_flags`.
- **Data-quality finding:** several top-ROR pairs (e.g. `linagliptin → CARDIOSPASM`) are
  mass-reporting / duplicate-cluster artifacts. The composite priority score + shrinkage
  correctly demote them below genuine signals.
- **NHANES (2017–2020 pre-pandemic):** survey-weighted prevalence, e.g. metformin 6.21%
  (unweighted n=960); GLP-1 agents flagged small-n.
- **PubMed:** real PMIDs retrieved for top non-artifact signals.
- **Bronze cache:** ~650 MB during build, **pruned after each run** → bucket ~0.37 MB (~$0/month).

### Provision and run

```bash
export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_DEFAULT_REGION=us-east-1
export BUCKET=pharmasignal-data-<suffix>

# 1. Provision S3 + Glue DB + Athena workgroup
PYTHONPATH=src python infrastructure/aws_deploy.py provision --bucket $BUCKET --region us-east-1

# 2. Run the pipeline straight to S3
export PHARMASIGNAL_DATA_ROOT=s3://$BUCKET
make pipeline-full

# 3. Register Glue external tables, then verify with Athena
PYTHONPATH=src python infrastructure/aws_deploy.py register --bucket $BUCKET --region us-east-1
PYTHONPATH=src python infrastructure/aws_deploy.py query    --bucket $BUCKET --region us-east-1
```

### Run the Streamlit dashboard against the cloud lakehouse

```bash
export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_DEFAULT_REGION=us-east-1
PHARMASIGNAL_DATA_ROOT=s3://$BUCKET PYTHONPATH=src streamlit run dashboard/app.py
```

### Terraform alternative

```bash
cd infrastructure/terraform && terraform init
terraform apply -var="bucket_name=pharmasignal-data-<unique-suffix>"
```

Outputs: bucket, Glue DB, Athena workgroup, and two IAM policy ARNs
(`pharmasignal-pipeline-write` for the pipeline principal,
`pharmasignal-dashboard-read` for the dashboard).

---

## FastAPI serving layer (Lambda + API Gateway)

The FastAPI layer decouples the frontend from S3 — the browser never holds AWS credentials.

```
Vercel (Next.js, ISR)  →  HTTP API Gateway  →  Lambda (FastAPI + Mangum)  →  S3 gold Parquet
```

The Lambda reads gold Parquet directly with DuckDB/pandas (not Athena per-request) — the
tables total ~0.36 MB, so direct Parquet reads are faster and cheaper.

### Endpoints

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness + visible tables/row counts + data source |
| `GET /dashboard/summary` | All six dashboard tables (`DashboardData`) — the primary frontend payload |
| `GET /signals?drug=&event=&drug_class=&flagged_only=&min_reports=&limit=` | Filterable disproportionality scores |
| `GET /emerging?priority=&limit=` | Emerging signals sorted by composite priority |
| `GET /drugs/{drug}` | One-drug profile: signals + emerging + NHANES + evidence |
| `GET /nhanes` | NHANES population context |
| `GET /evidence?drug=&event=` | PubMed evidence rows |
| `GET /interactions?drug=` | Drug-drug interaction signals |
| `GET /subgroups?drug=` | Demographic subgroup signals |

All read-only. Responses are JSON-safe (NaN/Inf → `null`).

### Run locally (no AWS, no Docker)

```bash
make install-api
make api-local      # uvicorn on :8000, reads PHARMASIGNAL_DATA_ROOT from .env
curl localhost:8000/health
curl localhost:8000/dashboard/summary | jq 'keys'
```

Point the frontend at it: set `NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL=http://localhost:8000`
in `frontend/.env.local`.

### Deploy to AWS

Requires Docker running and AWS creds with permission to manage ECR, Lambda, IAM, and
API Gateway v2.

```bash
python infrastructure/api_deploy.py deploy \
  --bucket pharmasignal-data-<suffix> \
  --cors-origins https://your-app.vercel.app \
  --region us-east-1
# or:
make api-deploy BUCKET=pharmasignal-data-<suffix> CORS=https://your-app.vercel.app
```

Prints the API base URL. HTTP APIs use the `$default` stage — no `/prod` segment.

```bash
python infrastructure/api_deploy.py url --region us-east-1
curl https://<api-id>.execute-api.us-east-1.amazonaws.com/health
```

### Redeploy after a code or data change

Re-run `deploy` — it rebuilds/pushes the image and calls `update-function-code`. Gold
data changes need **no redeploy**: the Lambda reads current S3 on a ≤5-min cache (tunable
via `PHARMASIGNAL_CACHE_TTL` Lambda env var).

### Tear down

```bash
python infrastructure/api_deploy.py destroy --region us-east-1
python infrastructure/api_deploy.py destroy --region us-east-1 --delete-ecr
```

### Security / cost

- **No static AWS keys in the request path.** Lambda uses an IAM role scoped to
  `s3:GetObject`/`s3:ListBucket` on the one bucket — read-only.
- **CORS** restricted to the origins passed via `--cors-origins`. Use `*` only for local testing.
- **Cost:** Lambda + HTTP API Gateway are pay-per-request; with ISR caching call volume
  is tiny. ~$0/month at typical traffic on top of ~$0.01/month S3 storage.

---

## Next.js frontend (Vercel)

### Local development

```bash
cd frontend
npm install
npm run dev    # http://localhost:3000
```

Without configuration the app uses the built-in demo payload (`lib/demo-data.ts`).
To use the live API: set `NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL` in `frontend/.env.local`.

### Deploy to Vercel

1. Vercel project root = `frontend/`.
2. Framework preset: **Next.js** · Install: `npm install` · Build: `npm run build` · Output: `.next`.
3. Project → Settings → Environment Variables:
   `NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL = https://<api-id>.execute-api.us-east-1.amazonaws.com`
4. Redeploy so the build inlines the value.

The frontend fetches **server-side** with `next: { revalidate: 300 }`, so pages are
ISR-cached and AWS is hit at most once per 5 minutes per route. If the API is unreachable,
the app falls back to the bundled demo payload.

**`NEXT_PUBLIC_*` is intentionally public (it's just a URL). Never put `AWS_*` or API keys
in a `NEXT_PUBLIC_*` var.**

### Verify

```bash
npm run typecheck
npm run build
```

---

## GitHub Actions (automated pipeline)

A ready workflow is at `.github/workflows/pipeline.yml`. It runs the pipeline on a
schedule and optionally syncs gold tables to S3.

Configure repo **Settings → Secrets and variables → Actions**:
- `OPENFDA_API_KEY`, `NCBI_API_KEY`, `NCBI_EMAIL` (optional).
- For S3 sync, prefer **OIDC** (no long-lived keys): create an IAM role trusting GitHub's
  OIDC provider, attach `pharmasignal-pipeline-write`, and set `AWS_ROLE_ARN` + `AWS_REGION`
  as repo variables.

---

## Full-history FAERS backfill (EMR Serverless)

For the one-time full-history backfill (2012Q4 → present, tens of GB) use the PySpark
path on EMR Serverless. Incremental quarterly runs use DuckDB (free on GitHub Actions).

### Cost expectations

| Step | When | Cost |
|---|---|---|
| `stage-faers` (download to bronze) | one-time | **~$0** (S3 ingress free; runs local/CI) |
| EMR Serverless `ingest` + `score` | one-time backfill | **~$1–4 ⚠️** (only costly step) |
| Incremental quarterly (`make gold-bulk`) | 4×/yr | **cents or $0** on GitHub Actions |
| S3 storage (silver+gold, few GB) | monthly | **< $0.20** |

Guardrails: the EMR app has a **maximum-capacity cap** (default 32 vCPU/128 GB) and a
**10-minute auto-stop**; the `score` command prints measured vCPU/GB-hours cost.

### Step-by-step

```bash
export PHARMASIGNAL_DATA_ROOT=s3://pharmasignal-data-XXXX
BUCKET=pharmasignal-data-XXXX

# 1. Stage raw ASCII to bronze (internet; ~$0).
make stage-faers QUARTERS="2012q4..2024q4"

# 2. Build the EMR dependency archive (scipy/pandas/numpy/pharmasignal + config).
bash spark/build_deps.sh                          # -> build/pharmasignal_deps.tar.gz

# 3. Provision EMR Serverless app + least-privilege IAM role (one-time).
python infrastructure/spark_backfill.py provision --bucket $BUCKET

# 4. Upload jobs + deps to S3.
python infrastructure/spark_backfill.py upload --bucket $BUCKET

# 5. Heavy compute (~$1–4). Each step waits and prints measured cost.
python infrastructure/spark_backfill.py ingest --quarters 2012q4..2024q4
python infrastructure/spark_backfill.py score

# 6. Register gold for Athena, then verify.
python infrastructure/aws_deploy.py register --bucket $BUCKET
python infrastructure/aws_deploy.py query    --bucket $BUCKET

# 7. (optional) Tear down the app — data stays in S3, idle cost is $0.
python infrastructure/spark_backfill.py teardown
```

Monitor and inspect costs:
```bash
python infrastructure/spark_backfill.py status
python infrastructure/spark_backfill.py costs   # vCPU/GB-hours -> $ estimate
```

### Incremental quarterly refresh (after backfill)

DuckDB handles one new quarter comfortably and runs free on GitHub Actions:
```bash
make stage-faers  QUARTERS="2025q2"
make ingest-faers QUARTERS="2025q2"   # bronze -> silver (pandas)
make gold-bulk                         # silver -> gold via SQL
```

### Local dry run (no AWS, $0)

```bash
pip install -r requirements-dev.txt   # includes pyspark (needs Java 17)
make stage-faers        QUARTERS="2023q4" DATA_ROOT=./data
make spark-ingest-local QUARTERS="2023q4" DATA_ROOT=./data
make spark-gold-local   DATA_ROOT=./data
```

### Notes

- **Scope = FAERS era (2012Q4+).** Legacy AERS extracts (pre-2012, `ISR`-keyed) need a
  separate reader — flagged as a follow-up item.
- **One scoring implementation.** Both DuckDB and Spark paths call
  `pharmasignal.pipeline.scoring` — ROR/PRR/EBGM cannot drift between engines.
- **Dependency archive must match the EMR runtime** — `spark/build_deps.sh` builds inside
  Amazon Linux 2023 so compiled wheels (scipy, numpy) match the execution environment.
- **Validate dedup** against FDA's published quarterly case totals before trusting counts
  at full scale.

---

## Cost summary

| Option | Monthly | Notes |
|---|---|---|
| Local + S3 + Streamlit Community Cloud | **$0–1** | Recommended MVP |
| AWS S3 + Athena + Glue | **$1–3** | Current live deployment |
| + Lambda API + HTTP API Gateway | **~$0** additional | Pay-per-request, ISR-cached |
| + Full FAERS silver+gold (few GB) | **~$0.10–0.20** S3 storage | |
| + EMR Serverless backfill (one-time) | **$1–4 one-time** | Then $0 idle |
| + Bedrock/Claude narratives (top-N batch) | **$0.50–2/run** | Scope-controlled; optional |
| **Total at scale** | **≈ $2–4/month** | |

Cost controls enforced in code:
- Parquet + partitioning; never scan raw ASCII on Athena.
- Precomputed gold; dashboard/API never recompute ROR/PRR.
- openFDA and PubMed responses cached in bronze by query hash.
- Bronze pruned after each build.
- Athena workgroup 1 GiB/query guardrail.
- Demo dataset bundled so reviewers incur **zero** cloud cost.
- `pipeline_health` persists `estimated_cost_usd` per run.

---

## Verification checklist

- [ ] `make demo && make test` pass locally with no cloud creds.
- [ ] `make pipeline` writes `data/gold/*.parquet` (needs network).
- [ ] Dashboard Pipeline Health page shows `source = openfda_api` after a live run.
- [ ] S3 bucket has public access blocked; no secrets in git (`git grep -i api_key`).
- [ ] Athena workgroup enforces the bytes-scanned cutoff.
- [ ] `curl https://<api-id>.execute-api.us-east-1.amazonaws.com/health` returns 200.
- [ ] Next.js frontend builds and typecheck passes (`npm run typecheck && npm run build`).
