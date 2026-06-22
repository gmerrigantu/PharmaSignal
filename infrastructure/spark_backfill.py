"""EMR Serverless orchestration for the whole-history FAERS backfill (boto3).

Why EMR Serverless: it is genuine serverless Spark — scales to zero between runs (no
idle cost), bills per vCPU/GB-second, and runs the *same* PySpark jobs in ``spark/jobs/``
that this repo tests locally. It is ~4x cheaper than Glue for equivalent work and needs
no cluster to manage.

Pipeline (see docs/backfill_orchestration.md):

  stage  (LOCAL / GitHub Actions — needs internet, writes to S3)   make stage-faers
     │   downloads FAERS ZIPs, extracts ASCII -> s3://bucket/bronze/faers/...
     ▼
  provision  -> EMR Serverless app + job-execution IAM role  (one-time)
  package    -> build pharmasignal source zip
  upload     -> push jobs + zip + deps archive to s3://bucket/spark/
  ingest     -> Spark job: bronze ASCII -> silver Parquet
  score      -> Spark job: silver -> scored gold marts
  costs      -> read the job run's resource utilization, print $ estimate
  teardown   -> delete the app (keeps data)

💸 COST: the EMR jobs (ingest+score over ~50 quarters) are the only step above a few
cents — expect roughly **$1–4 one-time**. Everything else (stage, provision, upload,
idle app) is ~$0. ``costs`` prints the actual figure after each run. Maximum capacity is
capped at provision time so a runaway job can't surprise you.

Credentials come from the standard AWS env chain. The app id + role arn are saved to
``infrastructure/.spark_backfill.json`` so later commands don't need flags.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REPO = Path(__file__).resolve().parents[1]
STATE_FILE = Path(__file__).resolve().parent / ".spark_backfill.json"
ROLE_NAME = "pharmasignal-emr-serverless-role"
APP_NAME = "pharmasignal-backfill"
EMR_RELEASE = "emr-7.5.0"

# EMR Serverless on-demand pricing, us-east-1 (USD). Update if the region/price changes.
PRICE_VCPU_HOUR = 0.052624
PRICE_MEMGB_HOUR = 0.0057785
PRICE_STORAGEGB_HOUR = 0.000111  # first 20 GB-hours/worker free; ignored in estimate


# --------------------------------------------------------------------------- #
# small state file so subsequent commands don't need --application-id flags
# --------------------------------------------------------------------------- #
def _load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def _save_state(**kw) -> None:
    state = _load_state()
    state.update(kw)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _require(state: dict, key: str) -> str:
    if key not in state:
        sys.exit(f"missing '{key}' — run `provision`/`upload` first or pass the flag")
    return state[key]


# --------------------------------------------------------------------------- #
# provision — IAM role + EMR Serverless application
# --------------------------------------------------------------------------- #
def _ensure_role(bucket: str, region: str) -> str:
    iam = boto3.client("iam", region_name=region)
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "emr-serverless.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }
    try:
        role = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="PharmaSignal EMR Serverless job execution role",
        )
        arn = role["Role"]["Arn"]
        print(f"[iam] created role {ROLE_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"[iam] role {ROLE_NAME} already exists")

    # Least-privilege: read/write only this bucket, plus its own logs + Glue catalog.
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject",
                                           "s3:DeleteObject", "s3:ListBucket"],
             "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"]},
            {"Effect": "Allow", "Action": ["glue:Get*", "glue:CreateTable",
                                           "glue:UpdateTable", "glue:BatchCreatePartition"],
             "Resource": "*"},
            {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream",
                                           "logs:PutLogEvents"], "Resource": "*"},
        ],
    }
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="pharmasignal-backfill-policy",
                        PolicyDocument=json.dumps(policy))
    print("[iam] attached least-privilege inline policy")
    return arn


def provision(bucket: str, region: str, max_vcpu: int, max_memory_gb: int) -> None:
    role_arn = _ensure_role(bucket, region)
    emr = boto3.client("emr-serverless", region_name=region)

    state = _load_state()
    app_id = state.get("application_id")
    if app_id:
        try:
            emr.get_application(applicationId=app_id)
            print(f"[emr] application {app_id} already exists")
        except ClientError:
            app_id = None

    if not app_id:
        app = emr.create_application(
            name=APP_NAME, releaseLabel=EMR_RELEASE, type="SPARK",
            # Scale to zero when idle (no cost), and cap total capacity (cost guardrail).
            autoStartConfiguration={"enabled": True},
            autoStopConfiguration={"enabled": True, "idleTimeoutMinutes": 10},
            maximumCapacity={"cpu": f"{max_vcpu} vCPU", "memory": f"{max_memory_gb} GB"},
        )
        app_id = app["applicationId"]
        print(f"[emr] created application {app_id} "
              f"(cap {max_vcpu} vCPU / {max_memory_gb} GB, autostop 10m)")

    _save_state(application_id=app_id, role_arn=role_arn, bucket=bucket, region=region)
    print(f"\n✅ provisioned. app={app_id}\n"
          f"   Next: `upload` then `ingest` / `score`.")


# --------------------------------------------------------------------------- #
# package + upload
# --------------------------------------------------------------------------- #
def package() -> Path:
    """Zip the pharmasignal source package (+ config) for shipping via --py-files."""
    out = REPO / "build" / "pharmasignal_src.zip"
    out.parent.mkdir(exist_ok=True)
    pkg = REPO / "src" / "pharmasignal"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in pkg.rglob("*.py"):
            zf.write(p, p.relative_to(REPO / "src"))
        for cfg in (REPO / "config").glob("*.yml"):
            zf.write(cfg, Path("pharmasignal_config") / cfg.name)
    print(f"[package] wrote {out} ({out.stat().st_size // 1024} KiB)")
    return out


def upload(bucket: str, region: str) -> None:
    s3 = boto3.client("s3", region_name=region)
    src_zip = package()

    uploads = {
        f"spark/jobs/{p.name}": p
        for p in (REPO / "spark" / "jobs").glob("*.py")
    }
    uploads["spark/pharmasignal_src.zip"] = src_zip
    deps = REPO / "build" / "pharmasignal_deps.tar.gz"
    if deps.exists():
        uploads["spark/pharmasignal_deps.tar.gz"] = deps
        print("[upload] including deps archive (scipy/pandas/etc.)")
    else:
        print("[upload] ⚠ no deps archive (build/pharmasignal_deps.tar.gz). "
              "Build it with spark/build_deps.sh — scipy is required for EBGM.")

    for key, path in uploads.items():
        s3.upload_file(str(path), bucket, key)
        print(f"[upload] s3://{bucket}/{key}")
    _save_state(bucket=bucket, region=region, has_deps=deps.exists())
    print("\n✅ upload complete.")


# --------------------------------------------------------------------------- #
# submit jobs
# --------------------------------------------------------------------------- #
def _spark_params(bucket: str, has_deps: bool) -> str:
    common = "s3://%s/spark/jobs/_common.py" % bucket
    src = "s3://%s/spark/pharmasignal_src.zip" % bucket
    params = [
        "--conf spark.executor.cores=4",
        "--conf spark.executor.memory=14g",
        "--conf spark.driver.cores=4",
        "--conf spark.driver.memory=14g",
        f"--py-files {common},{src}",
    ]
    if has_deps:
        # Ship a packed venv (scipy/pandas/numpy/pyyaml + pharmasignal) and point the
        # Spark Pythons at it. Built for the EMR Linux image by spark/build_deps.sh.
        archive = "s3://%s/spark/pharmasignal_deps.tar.gz#environment" % bucket
        py = "./environment/bin/python"
        cfg = "./environment/pharmasignal_config"
        params += [
            f"--conf spark.archives={archive}",
            f"--conf spark.emr-serverless.driverEnv.PYSPARK_PYTHON={py}",
            f"--conf spark.executorEnv.PYSPARK_PYTHON={py}",
            # The driver reads YAML config; point it at the copy bundled in the archive.
            f"--conf spark.emr-serverless.driverEnv.PHARMASIGNAL_CONFIG_DIR={cfg}",
        ]
    return " ".join(params)


def _submit(entry: str, args: list[str]) -> str:
    state = _load_state()
    app_id = _require(state, "application_id")
    role_arn = _require(state, "role_arn")
    bucket = _require(state, "bucket")
    region = state.get("region", "us-east-1")
    emr = boto3.client("emr-serverless", region_name=region)

    run = emr.start_job_run(
        applicationId=app_id,
        executionRoleArn=role_arn,
        name=f"pharmasignal-{entry}",
        jobDriver={"sparkSubmit": {
            "entryPoint": f"s3://{bucket}/spark/jobs/{entry}.py",
            "entryPointArguments": args,
            "sparkSubmitParameters": _spark_params(bucket, state.get("has_deps", False)),
        }},
        configurationOverrides={"monitoringConfiguration": {
            "s3MonitoringConfiguration": {"logUri": f"s3://{bucket}/spark-logs/"}}},
    )
    job_id = run["jobRunId"]
    _save_state(last_job_id=job_id)
    print(f"[emr] submitted {entry} job {job_id}")
    return job_id


def _wait(job_id: str, *, poll: float = 15.0) -> dict:
    state = _load_state()
    emr = boto3.client("emr-serverless", region_name=state.get("region", "us-east-1"))
    app_id = state["application_id"]
    terminal = {"SUCCESS", "FAILED", "CANCELLED"}
    while True:
        jr = emr.get_job_run(applicationId=app_id, jobRunId=job_id)["jobRun"]
        st = jr["state"]
        print(f"[emr] {job_id} -> {st}", flush=True)
        if st in terminal:
            if st != "SUCCESS":
                print(f"[emr] state detail: {jr.get('stateDetails')}")
            _print_cost(jr)
            return jr
        time.sleep(poll)


def ingest(quarters: list[str], wait: bool) -> None:
    bucket = _require(_load_state(), "bucket")
    args = ["--data-root", f"s3://{bucket}"]
    if quarters:
        args += ["--quarters", *quarters]
    job = _submit("ingest_faers_spark", args)
    if wait:
        _wait(job)


def score(wait: bool) -> None:
    bucket = _require(_load_state(), "bucket")
    job = _submit("build_gold_spark", ["--data-root", f"s3://{bucket}"])
    if wait:
        _wait(job)


# --------------------------------------------------------------------------- #
# cost reporting
# --------------------------------------------------------------------------- #
def _print_cost(job_run: dict) -> None:
    util = job_run.get("totalResourceUtilization") or {}
    vcpu_h = util.get("vCPUHour", 0.0)
    mem_h = util.get("memoryGBHour", 0.0)
    cost = vcpu_h * PRICE_VCPU_HOUR + mem_h * PRICE_MEMGB_HOUR
    print(f"[cost] vCPU-hours={vcpu_h:.3f} memGB-hours={mem_h:.3f} "
          f"-> ≈ ${cost:.3f} (us-east-1 on-demand)")


def costs(job_id: str | None) -> None:
    state = _load_state()
    emr = boto3.client("emr-serverless", region_name=state.get("region", "us-east-1"))
    job_id = job_id or state.get("last_job_id")
    if not job_id:
        sys.exit("no job id (pass --job-id or run a job first)")
    jr = emr.get_job_run(applicationId=state["application_id"], jobRunId=job_id)["jobRun"]
    print(f"job {job_id}: {jr['state']}")
    _print_cost(jr)


def status(job_id: str | None) -> None:
    state = _load_state()
    emr = boto3.client("emr-serverless", region_name=state.get("region", "us-east-1"))
    app = emr.get_application(applicationId=state["application_id"])["application"]
    print(f"app {app['applicationId']}: {app['state']} ({EMR_RELEASE})")
    job_id = job_id or state.get("last_job_id")
    if job_id:
        jr = emr.get_job_run(applicationId=app["applicationId"], jobRunId=job_id)["jobRun"]
        print(f"last job {job_id}: {jr['state']} {jr.get('stateDetails', '')}")


def teardown() -> None:
    state = _load_state()
    if "application_id" not in state:
        print("nothing to tear down")
        return
    emr = boto3.client("emr-serverless", region_name=state.get("region", "us-east-1"))
    try:
        emr.stop_application(applicationId=state["application_id"])
        emr.delete_application(applicationId=state["application_id"])
        print(f"[emr] deleted application {state['application_id']} (S3 data kept)")
    except ClientError as e:
        print(f"[emr] {e.response['Error']['Code']}")
    STATE_FILE.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("provision")
    sp.add_argument("--bucket", required=True)
    sp.add_argument("--region", default="us-east-1")
    sp.add_argument("--max-vcpu", type=int, default=32, help="capacity cap (cost guard)")
    sp.add_argument("--max-memory-gb", type=int, default=128)

    su = sub.add_parser("upload")
    su.add_argument("--bucket")
    su.add_argument("--region", default="us-east-1")

    sub.add_parser("package")

    si = sub.add_parser("ingest")
    si.add_argument("--quarters", nargs="*", default=[])
    si.add_argument("--no-wait", action="store_true")

    ss_ = sub.add_parser("score")
    ss_.add_argument("--no-wait", action="store_true")

    sc = sub.add_parser("costs")
    sc.add_argument("--job-id")
    st = sub.add_parser("status")
    st.add_argument("--job-id")
    sub.add_parser("teardown")

    args = p.parse_args()
    if args.cmd == "provision":
        provision(args.bucket, args.region, args.max_vcpu, args.max_memory_gb)
    elif args.cmd == "upload":
        state = _load_state()
        upload(args.bucket or state.get("bucket"), args.region or state.get("region", "us-east-1"))
    elif args.cmd == "package":
        package()
    elif args.cmd == "ingest":
        ingest(args.quarters, wait=not args.no_wait)
    elif args.cmd == "score":
        score(wait=not args.no_wait)
    elif args.cmd == "costs":
        costs(args.job_id)
    elif args.cmd == "status":
        status(args.job_id)
    elif args.cmd == "teardown":
        teardown()


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "src"))
    main()
