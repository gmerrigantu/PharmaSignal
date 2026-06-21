"""PharmaSignal serving API (requirements §13.1).

A thin read-only HTTP layer over the **gold** lakehouse tables. It is the boundary
between the data backend (S3 Parquet, read with DuckDB/pandas) and any external
frontend (the Next.js app on Vercel). AWS credentials never leave the server side:
the Lambda assumes an IAM role with read-only S3 access — no static keys in the
browser, which is why the frontend must NOT query Athena/S3 directly.

Run locally:   uvicorn pharmasignal.api.main:app --reload
Deploy:        infrastructure/api_deploy.py  (Lambda container image + HTTP API Gateway)
"""
