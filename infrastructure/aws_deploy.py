"""PharmaSignal AWS deploy/provisioning (boto3) — the runnable cloud setup.

This is infrastructure-as-code in Python (a lightweight alternative to the Terraform
in terraform/, runnable here without the terraform binary). It provisions the minimal
least-privilege lakehouse and registers gold tables for Athena.

Subcommands:
  provision   Create S3 bucket (locked down), Glue database, Athena workgroup.
  register    Create Athena/Glue external tables over the gold Parquet in S3.
  query       Run a verification Athena query and print results.
  status      Print what exists.

Usage:
  python infrastructure/aws_deploy.py provision --bucket pharmasignal-data-XXuser --region us-east-1
  python infrastructure/aws_deploy.py register  --bucket pharmasignal-data-XXuser
  python infrastructure/aws_deploy.py query     --bucket pharmasignal-data-XXuser

Credentials come from the standard AWS env chain (AWS_ACCESS_KEY_ID/SECRET/REGION).
"""
from __future__ import annotations

import argparse
import sys
import time

import boto3
import pandas as pd
from botocore.exceptions import ClientError

GLUE_DB = "pharmasignal"
ATHENA_WORKGROUP = "pharmasignal"
GOLD_TABLES = [
    "signal_scores", "signal_scores_all", "drug_event_counts", "emerging_signals", "drug_label_flags",
    "subgroup_signals", "interaction_signals",
    "nhanes_population_context", "pubmed_evidence", "pubmed_support_summary",
    "pipeline_health", "data_quality_checks",
]

# pandas dtype -> Athena (Hive) type
_TYPE_MAP = {
    "object": "string", "string": "string",
    "int64": "bigint", "int32": "int", "Int64": "bigint",
    "float64": "double", "float32": "float",
    "bool": "boolean", "boolean": "boolean",
    "datetime64[ns]": "timestamp", "datetime64[ns, UTC]": "timestamp",
}


def _athena_type(dtype: str) -> str:
    return _TYPE_MAP.get(str(dtype), "string")


# --------------------------------------------------------------------------- #
# provision
# --------------------------------------------------------------------------- #
def provision(bucket: str, region: str) -> None:
    s3 = boto3.client("s3", region_name=region)
    glue = boto3.client("glue", region_name=region)
    athena = boto3.client("athena", region_name=region)

    # 1) S3 bucket (us-east-1 must omit LocationConstraint).
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(Bucket=bucket,
                             CreateBucketConfiguration={"LocationConstraint": region})
        print(f"[s3] created bucket {bucket}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            print(f"[s3] bucket {bucket} already exists")
        else:
            raise

    # 2) Lock it down + versioning + encryption + lifecycle.
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True,
            "BlockPublicPolicy": True, "RestrictPublicBuckets": True},
    )
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
    )
    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={"Rules": [
            {"ID": "expire-athena-results", "Status": "Enabled",
             "Filter": {"Prefix": "athena-results/"}, "Expiration": {"Days": 14}},
            {"ID": "expire-tmp", "Status": "Enabled",
             "Filter": {"Prefix": "tmp/"}, "Expiration": {"Days": 7}},
        ]},
    )
    print("[s3] public access blocked, versioning + AES256 + lifecycle set")

    # 3) Glue database.
    try:
        glue.create_database(DatabaseInput={"Name": GLUE_DB,
                             "Description": "PharmaSignal gold marts"})
        print(f"[glue] created database {GLUE_DB}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "AlreadyExistsException":
            print(f"[glue] database {GLUE_DB} already exists")
        else:
            raise

    # 4) Athena workgroup with a per-query bytes-scanned guardrail + results location.
    try:
        athena.create_work_group(
            Name=ATHENA_WORKGROUP,
            Configuration={
                "ResultConfiguration": {"OutputLocation": f"s3://{bucket}/athena-results/"},
                "EnforceWorkGroupConfiguration": True,
                "PublishCloudWatchMetricsEnabled": True,
                "BytesScannedCutoffPerQuery": 1_073_741_824,  # 1 GiB guardrail
            },
            Description="PharmaSignal — cost-guarded analytics workgroup",
        )
        print(f"[athena] created workgroup {ATHENA_WORKGROUP}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidRequestException":
            print(f"[athena] workgroup {ATHENA_WORKGROUP} already exists")
        else:
            raise
    print("\n✅ provision complete.")


# --------------------------------------------------------------------------- #
# register — create external tables over gold Parquet in S3
# --------------------------------------------------------------------------- #
def _run_athena(athena, sql: str, bucket: str, region: str) -> str:
    qid = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": GLUE_DB},
        WorkGroup=ATHENA_WORKGROUP,
        ResultConfiguration={"OutputLocation": f"s3://{bucket}/athena-results/"},
    )["QueryExecutionId"]
    while True:
        st = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        state = st["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            if state != "SUCCEEDED":
                raise RuntimeError(f"Athena query {state}: {st.get('StateChangeReason')}\nSQL: {sql[:200]}")
            return qid
        time.sleep(1.0)


def register(bucket: str, region: str) -> None:
    from pharmasignal.serving import storage  # noqa: E402

    athena = boto3.client("athena", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    registered = 0
    for name in GOLD_TABLES:
        key = f"gold/{name}.parquet"
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except ClientError:
            print(f"[skip] s3://{bucket}/{key} not found")
            continue
        # Infer schema from the Parquet (read just the columns/dtypes).
        df = storage.read_parquet(f"s3://{bucket}/{key}")
        cols = ",\n  ".join(f"`{c}` {_athena_type(t)}" for c, t in df.dtypes.items())
        # External table whose LOCATION is the *folder* containing the parquet object.
        # We give each table its own prefix to satisfy Athena's folder-location rule.
        prefix = f"gold_tables/{name}/"
        s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": key},
                       Key=f"{prefix}{name}.parquet")
        ddl = (
            f"CREATE EXTERNAL TABLE IF NOT EXISTS `{name}` (\n  {cols}\n)\n"
            f"STORED AS PARQUET\nLOCATION 's3://{bucket}/{prefix}'\n"
            f"TBLPROPERTIES ('parquet.compression'='SNAPPY');"
        )
        _run_athena(athena, f"DROP TABLE IF EXISTS `{name}`", bucket, region)
        _run_athena(athena, ddl, bucket, region)
        print(f"[glue] registered table {name} ({len(df.columns)} cols)")
        registered += 1
    print(f"\n✅ registered {registered} gold tables in Glue DB '{GLUE_DB}'.")


# --------------------------------------------------------------------------- #
# query — verification
# --------------------------------------------------------------------------- #
def query(bucket: str, region: str) -> None:
    athena = boto3.client("athena", region_name=region)
    sql = (
        "SELECT drug_name_normalized, adverse_event, a_drug_event, "
        "round(ror,2) ror, round(prr,2) prr, disproportionality_flag "
        "FROM signal_scores ORDER BY ror DESC LIMIT 10"
    )
    qid = _run_athena(athena, sql, bucket, region)
    rows = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
    header = [c.get("VarCharValue", "") for c in rows[0]["Data"]]
    data = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows[1:]]
    print("Athena query: top signals by ROR (from S3 gold via Glue/Athena)\n")
    print(pd.DataFrame(data, columns=header).to_string(index=False))


def status(bucket: str, region: str) -> None:
    s3 = boto3.client("s3", region_name=region)
    glue = boto3.client("glue", region_name=region)
    try:
        objs = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
        print(f"[s3] {bucket}: {len(objs)} objects")
    except ClientError as e:
        print(f"[s3] {e.response['Error']['Code']}")
    try:
        tables = glue.get_tables(DatabaseName=GLUE_DB)["TableList"]
        print(f"[glue] {GLUE_DB}: {[t['Name'] for t in tables]}")
    except ClientError as e:
        print(f"[glue] {e.response['Error']['Code']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["provision", "register", "query", "status"])
    p.add_argument("--bucket", required=True)
    p.add_argument("--region", default="us-east-1")
    args = p.parse_args()
    {"provision": provision, "register": register, "query": query, "status": status}[
        args.command](args.bucket, args.region)


if __name__ == "__main__":
    sys.path.insert(0, "src")
    main()
