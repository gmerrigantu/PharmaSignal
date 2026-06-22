# Full-history FAERS backfill — heavy compute orchestration

This is the runbook for the one-time whole-history backfill (FAERS 2012Q4 → present) and
the cheap incremental quarterly refresh that follows. It implements WS1/WS8 of
[FULL_FAERS_UPGRADE_PLAN.md](FULL_FAERS_UPGRADE_PLAN.md): **Spark for the heavy data
work, on AWS EMR Serverless, with cost held to a few dollars one-time and ~$0 ongoing.**

## Why EMR Serverless (and why not Glue / always-on EMR)

| Option | Cost for this workload | Notes |
|---|---|---|
| **EMR Serverless** ✅ | **~$1–4 one-time**, $0 idle | Serverless Spark, per-second billing, scales to zero, capacity-capped. Runs our jobs unchanged. |
| AWS Glue | ~$4+/run | $0.44/DPU-hr, 1-DPU minimum granularity; pricier per unit. |
| EMR on EC2 (Spot) | ~$0.25/hr raw | Cheapest compute but you manage a cluster; overkill here. |
| Local DuckDB (`make gold-bulk`) | $0 | Great for incremental/dev; the bulk backfill is what we use Spark for. |

The same PySpark jobs in `spark/jobs/` run locally (`local[*]`) and on EMR Serverless —
only `--data-root` changes (`./data` vs `s3://bucket`).

## 💸 Cost expectations (read before running)

| Step | When | Cost |
|---|---|---|
| `stage` (download ~tens of GB, extract to S3) | one-time | **~$0** (S3 ingress free; run local/CI) |
| `provision` / `upload` / idle app | — | **~$0** |
| **`ingest` + `score` on EMR Serverless** | one-time backfill | **~$1–4** ⚠️ the only step over a few cents |
| Incremental quarterly (`stage` + DuckDB `make gold-bulk`, or one Spark run) | 4×/yr | **cents** (or $0 on GitHub Actions) |
| S3 storage (silver+gold, few GB) | monthly | **<$0.20** |

Guardrails in place: the EMR app has a **maximum-capacity cap** (default 32 vCPU/128 GB)
and a 10-minute **auto-stop**; `score` prints the actual `$` from the job's measured
vCPU/GB-hours. Staging deliberately runs *off* EMR so we never pay for a NAT gateway
(EMR jobs only touch S3, which needs no internet egress).

## Architecture

```
FAERS ZIPs (fda.gov)
  │  STAGE  (local / GitHub Actions — internet)        make stage-faers QUARTERS=...
  ▼
s3://bucket/bronze/faers/year=/quarter=/ascii/*.txt
  │  INGEST  (EMR Serverless Spark)                    spark_backfill.py ingest
  ▼   parse $-ASCII · normalize drugs (distinct) · partition
s3://bucket/silver/faers/{reports,drugs,reactions,therapies,report_sources,...}
  │  SCORE  (EMR Serverless Spark)                     spark_backfill.py score
  ▼   global case-version dedup + DELETED exclusion · marginals · co-occurrence
  │   → driver-side EBGM/ROR via pharmasignal.pipeline.scoring (one impl)
s3://bucket/gold/{signal_scores, signal_scores_all, emerging_signals, ...}
  │  REGISTER (Athena/Glue)                            infrastructure/aws_deploy.py register
  ▼
FastAPI + dashboard (unchanged, read-only gold)
```

## Prerequisites

- AWS creds in the environment (the same account/bucket used by `aws_deploy.py`).
- Docker (only for `spark/build_deps.sh`, which builds the dependency archive).
- A provisioned lakehouse bucket (`infrastructure/aws_deploy.py provision`).

## One-time backfill — step by step

```bash
export PHARMASIGNAL_DATA_ROOT=s3://pharmasignal-data-XXXX
BUCKET=pharmasignal-data-XXXX

# 1. Stage raw ASCII to bronze (internet; ~$0). Pick your history start.
make stage-faers QUARTERS="2012q4..2024q4"

# 2. Build the EMR dependency archive (scipy/pandas/numpy/pharmasignal + config).
bash spark/build_deps.sh                       # -> build/pharmasignal_deps.tar.gz

# 3. Provision EMR Serverless app + least-privilege IAM role (one-time).
python infrastructure/spark_backfill.py provision --bucket $BUCKET

# 4. Upload jobs + deps to S3.
python infrastructure/spark_backfill.py upload --bucket $BUCKET

# 5. Heavy compute (⚠️ ~$1–4). Each waits and prints its measured cost.
python infrastructure/spark_backfill.py ingest --quarters 2012q4..2024q4
python infrastructure/spark_backfill.py score

# 6. Register gold for Athena, then verify.
python infrastructure/aws_deploy.py register --bucket $BUCKET
python infrastructure/aws_deploy.py query    --bucket $BUCKET

# 7. (optional) delete the app — data stays in S3, idle cost is already $0.
python infrastructure/spark_backfill.py teardown
```

`status` and `costs` inspect the latest run:

```bash
python infrastructure/spark_backfill.py status
python infrastructure/spark_backfill.py costs        # vCPU/GB-hours -> $ estimate
```

## Incremental quarterly refresh (cheap)

When FDA publishes a new quarter, you don't need Spark at all — DuckDB handles one
quarter comfortably and runs free on GitHub Actions:

```bash
make stage-faers QUARTERS="2025q1"     # or `make ingest-faers` for the pandas path
make ingest-faers QUARTERS="2025q1"    # bronze ZIP -> silver (pandas)
make gold-bulk                         # silver -> gold via DuckDB
```

Use the Spark path again only for a full re-score over the whole history.

## Local dry run (no AWS, $0)

The jobs run in local Spark, exercised by `tests/test_spark_backfill.py`:

```bash
pip install -r requirements-dev.txt    # brings in pyspark (needs Java 17)
make stage-faers       QUARTERS="2023q4" DATA_ROOT=./data    # needs internet
make spark-ingest-local QUARTERS="2023q4" DATA_ROOT=./data
make spark-gold-local   DATA_ROOT=./data
```

## Notes & follow-ups

- **Scope = FAERS era (2012Q4+).** The legacy AERS extracts (pre-2012, `ISR`-keyed,
  different filenames) need a separate reader; flagged as a follow-up.
- **One scoring implementation.** Both the DuckDB (`build_gold_bulk`) and Spark
  (`build_gold_spark`) paths call `pharmasignal.pipeline.scoring`, so ROR/PRR/EBGM can't
  drift between engines.
- **Dependency archive must match the EMR runtime** — that's why `build_deps.sh` builds
  it inside Amazon Linux 2023 (scipy ships compiled wheels).
- **Validate dedup** against FDA's published quarterly case totals before trusting counts
  at full scale (WS1 risk note).
