# PharmaSignal — Cloud Setup Instructions

This is the **explicit, step-by-step** guide for the cloud infrastructure. Nothing
here is required to run the demo locally (`make demo && make dashboard`) — it is for
moving to a live, cloud-deployed pipeline.

> **Security rules (apply throughout):** never commit credentials or API keys; use
> environment variables / secret stores; the public dashboard must be read-only; use
> least-privilege IAM. See [../docs/limitations.md](../docs/limitations.md).

---

## 0. Prerequisites & secrets (do this first)

| Secret | Where to get it | How PharmaSignal reads it |
|---|---|---|
| `OPENFDA_API_KEY` | https://open.fda.gov/apis/authentication/ (free) | env var, optional but raises rate limit |
| `NCBI_API_KEY` | https://www.ncbi.nlm.nih.gov/account/ → API Key Management | env var, optional |
| `NCBI_EMAIL` | your email (NCBI etiquette) | env var |
| AWS creds (Option B) | IAM user / OIDC role | standard AWS SDK chain |

Create a local `.env` from the template and **never commit it**:
```bash
cp .env.example .env       # then edit values
```

The `.gitignore` already excludes `.env`, `*.env`, and `.streamlit/secrets.toml`.

---

## 1. Recommended MVP path — Local pipeline + S3 + Streamlit Community Cloud

**Cost: ≈ $0–1/month.** No always-on compute.

1. **Run the pipeline locally** (or in GitHub Actions, step 3):
   ```bash
   make install
   make pipeline      # openFDA -> data/gold/*.parquet
   make nhanes        # optional, downloads NHANES XPT
   make pubmed        # optional, PubMed evidence for top signals
   ```
2. **Create an S3 bucket** for the gold tables (console or CLI):
   ```bash
   aws s3 mb s3://pharmasignal-data-<unique-suffix>
   aws s3api put-public-access-block --bucket pharmasignal-data-<unique-suffix> \
     --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
   ```
3. **Publish gold tables**:
   ```bash
   aws s3 sync data/gold "s3://pharmasignal-data-<unique-suffix>/gold/"
   ```
4. **Deploy the dashboard to Streamlit Community Cloud**:
   - Push this repo to GitHub.
   - On https://share.streamlit.io → *New app* → pick the repo →
     **main file path = `dashboard/app.py`**.
   - In *Advanced settings → Secrets*, add any keys (e.g. `OPENFDA_API_KEY`).
   - The app reads bundled `sample_data/gold` by default. To read live data, either
     commit the (small) gold Parquet or have the app pull from S3 on startup and set
     `PHARMASIGNAL_DATA_ROOT` accordingly.

---

## 2. AWS data-engineering path — S3 + Athena + Glue (Option B)

**Cost: ≈ $1–3/month** with partitioned Parquet + the query guardrail.

There are two ways to provision: **Terraform** (declarative IaC) or the runnable
**boto3 deploy script** (`infrastructure/aws_deploy.py`, no terraform binary needed).
The boto3 path is what this project was actually deployed with.

### 2a. boto3 deploy script (recommended, runnable now)
```bash
# Credentials via the standard env chain (never commit them):
export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_DEFAULT_REGION=us-east-1
export BUCKET=pharmasignal-data-<account-or-suffix>

# 1) Provision S3 (locked down) + Glue DB + Athena workgroup
PYTHONPATH=src python infrastructure/aws_deploy.py provision --bucket $BUCKET --region us-east-1

# 2) Run the pipeline straight to S3 (the lakehouse backend is chosen by this env var)
export PHARMASIGNAL_DATA_ROOT=s3://$BUCKET
export OPENFDA_API_KEY=...        # optional, raises rate limit
PYTHONPATH=src python -m pharmasignal.pipeline.build_gold
PYTHONPATH=src python -m pharmasignal.nhanes.ingest
PYTHONPATH=src python -m pharmasignal.pubmed.build_evidence --top 20

# 3) Register Glue external tables over the S3 gold Parquet, then verify with Athena
PYTHONPATH=src python infrastructure/aws_deploy.py register --bucket $BUCKET --region us-east-1
PYTHONPATH=src python infrastructure/aws_deploy.py query    --bucket $BUCKET --region us-east-1
```

The same `PHARMASIGNAL_DATA_ROOT=s3://$BUCKET` makes the **dashboard** read gold from
S3 with no code changes:
```bash
PHARMASIGNAL_DATA_ROOT=s3://$BUCKET PYTHONPATH=src streamlit run dashboard/app.py
```

### 2b. Terraform (declarative alternative)
```bash
cd infrastructure/terraform && terraform init
terraform apply -var="bucket_name=pharmasignal-data-<unique-suffix>"
```
Outputs: bucket, Glue DB, Athena workgroup, and two IAM policy ARNs (attach
`pharmasignal-pipeline-write` to the pipeline principal, `pharmasignal-dashboard-read`
to the dashboard). The per-query bytes-scanned cutoff prevents runaway scans.

---

## 3. Automated runs — GitHub Actions (free tier)

A ready workflow is at [`.github/workflows/pipeline.yml`](../.github/workflows/pipeline.yml).
It runs the pipeline on a schedule and (optionally) syncs gold tables to S3.

Configure repo **Settings → Secrets and variables → Actions**:
- `OPENFDA_API_KEY`, `NCBI_API_KEY`, `NCBI_EMAIL` (optional)
- For S3 sync, prefer **OIDC** (no long-lived keys): create an IAM role trusting
  GitHub's OIDC provider, attach `pharmasignal-pipeline-write`, and set
  `AWS_ROLE_ARN` + `AWS_REGION` repo variables. (Or, less ideal, set
  `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` secrets.)

---

## 4. Databricks path (Option C)
Use Databricks Free/Community Edition for the Delta/lakehouse demo: import the `src/`
modules into a notebook/job, write gold tables as Delta to managed storage, and serve
via Databricks SQL or a Databricks App. Platform cost is $0 with feature limits.

---

## 5. Verification checklist
- [ ] `make demo && make test` pass locally with no cloud creds.
- [ ] `make pipeline` writes `data/gold/*.parquet` (needs network).
- [ ] Dashboard "Pipeline Health" page shows `source = openfda_api` after a live run.
- [ ] S3 bucket has public access blocked; no secrets in git (`git grep -i api_key`).
- [ ] Athena workgroup enforces the bytes-scanned cutoff.
