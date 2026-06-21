# Terraform — PharmaSignal AWS infrastructure (optional)

Minimal, least-privilege resources for the AWS deployment path. **Optional for the
MVP** — the local + Streamlit Community Cloud path needs none of this.

## What it creates
- S3 lakehouse bucket (versioned, encrypted, public access blocked, lifecycle rules)
- Glue catalog database `pharmasignal`
- Athena workgroup with a per-query bytes-scanned guardrail + results location
- Two IAM policies: read-only (dashboard) and write (ingestion pipeline)
- An (empty) Secrets Manager entry for API keys

## Usage
```bash
cd infrastructure/terraform
terraform init
terraform apply -var="bucket_name=pharmasignal-data-<your-unique-suffix>"
```

Then point the app at the bucket:
```bash
export PHARMASIGNAL_DATA_ROOT=s3://pharmasignal-data-<suffix>   # if using an S3 fuse mount
# or sync gold tables down for Athena/DuckDB querying
aws s3 sync data/gold "s3://pharmasignal-data-<suffix>/gold/"
```

## Notes
- Attach `dashboard_read_policy_arn` to the principal that runs the dashboard and
  `pipeline_write_policy_arn` to the principal that runs `build_gold`.
- `terraform destroy` removes everything; the bucket must be emptied first.
- State is local by default — for team use, configure an S3 backend.
