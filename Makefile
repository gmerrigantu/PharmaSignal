# PharmaSignal — common workflows.
# Uses the bundled FDA-AE virtualenv if present, else system python3.
PY ?= $(shell [ -x FDA-AE/bin/python ] && echo FDA-AE/bin/python || echo python3)
PIP ?= $(PY) -m pip
PYTHONPATH ?= src
ENV_FILE ?= .env
ENV_PREFIX = set -a; [ ! -f $(ENV_FILE) ] || . $(ENV_FILE); set +a;

.PHONY: help install install-dev install-api demo pipeline pipeline-aws ingest-faers gold-bulk drug-dimension backfill-local stage-faers spark-ingest-local spark-gold-local nhanes pubmed labels sync-aws dashboard dashboard-aws api-local api-deploy api-url test lint clean

help:
	@echo "PharmaSignal make targets:"
	@echo "  install          Install runtime dependencies"
	@echo "  install-dev      Install runtime + dev/test dependencies"
	@echo "  demo             Generate the bundled offline demo dataset into data/gold"
	@echo "  pipeline         Run the openFDA -> silver/gold pipeline (API mode, demo scope)"
	@echo "  pipeline-aws     Run pipeline using .env, e.g. PHARMASIGNAL_DATA_ROOT=s3://bucket"
	@echo "  ingest-faers     Download FAERS quarterly ZIPs -> silver (QUARTERS=\"2023q1..2023q4\")"
	@echo "                   Skips quarters already in silver; --force to override"
	@echo "                   --prune-bronze deletes each ZIP after ingest (saves ~300 MB/quarter)"
	@echo "  gold-bulk        Score the whole drug x event matrix from silver via SQL (no API)"
	@echo "                   Env: PHARMASIGNAL_DUCKDB_MEMORY_LIMIT (default 4gb)"
	@echo "                        PHARMASIGNAL_DUCKDB_THREADS (default cpu_count/2)"
	@echo "  backfill-local   Full backfill: ingest 2012q4..2025q4 + score (resumable)"
	@echo "                   Skips already-ingested quarters; prunes bronze ZIPs; safe for laptops"
	@echo "                   Override range: make backfill-local QUARTERS=\"2018q1..2025q4\""
	@echo "  stage-faers      Download+extract FAERS ASCII -> bronze for Spark (QUARTERS=...)"
	@echo "  spark-ingest-local  Run the PySpark ingest job locally (bronze -> silver)"
	@echo "  spark-gold-local    Run the PySpark gold/score job locally (silver -> gold)"
	@echo "  drug-dimension   RxNorm ingredient map over distinct silver drugs (needs network)"
	@echo "                   Re-keys gold-bulk to ingredient level. ARGS=\"--limit 5000\" to cap"
	@echo "  nhanes           Ingest NHANES population context (needs network, downloads XPT)"
	@echo "  pubmed           Fetch PubMed evidence for top signals (needs network)"
	@echo "  labels           Flag each signal labeled-vs-novel via openFDA Drug Label API"
	@echo "  enrich           Join PubMed+NHANES into emerging_signals, recompute priority"
	@echo "  pipeline-full    Run pipeline -> nhanes -> pubmed -> enrich in order"
	@echo "  dashboard        Launch the Streamlit dashboard"
	@echo "  dashboard-aws    Launch dashboard using .env, e.g. PHARMASIGNAL_DATA_ROOT=s3://bucket"
	@echo "  api-local        Run the FastAPI serving layer locally (uvicorn :8000)"
	@echo "  api-deploy       Build+push image and deploy Lambda + HTTP API (BUCKET=, CORS=)"
	@echo "  api-url          Print the deployed API base URL"
	@echo "  test             Run unit tests"
	@echo "  clean            Remove generated lakehouse data (keeps sample_data/)"

install:
	$(PIP) install -r requirements.txt

install-dev:
	$(PIP) install -r requirements-dev.txt

install-api:
	$(PIP) install -r requirements-api.txt uvicorn

demo:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pipeline.generate_demo

pipeline:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pipeline.build_gold

pipeline-aws:
	$(ENV_PREFIX) PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pipeline.build_gold

# Whole-database production path (WS1/WS2). QUARTERS accepts ranges + lists, e.g.
#   make ingest-faers QUARTERS="2021q1..2023q4"
# With no QUARTERS it falls back to the list in config/faers_quarters.yml.
ingest-faers:
	$(ENV_PREFIX) PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.ingestion.faers_quarterly $(QUARTERS)

# LOCAL_DATA_ROOT is always the repo's data/ dir regardless of PHARMASIGNAL_DATA_ROOT in
# .env. Targets that read the LOCAL silver layer (gold-bulk, drug-dimension) must pin it so
# .env's PHARMASIGNAL_DATA_ROOT=s3://... doesn't point them at S3 (where silver isn't
# uploaded — only gold is). Build locally over local silver, then upload gold to S3.
LOCAL_DATA_ROOT := $(CURDIR)/data

# Set-based SQL scoring over silver -> gold (no network). Run after ingest-faers.
# Picks up gold/drug_dimension.parquet automatically (Option B: ingredient re-aggregation).
gold-bulk:
	$(ENV_PREFIX) PHARMASIGNAL_DATA_ROOT=$(LOCAL_DATA_ROOT) PYTHONPATH=$(PYTHONPATH) $(PY) \
		-m pharmasignal.pipeline.build_gold_bulk

# RxNorm ingredient map over the distinct silver drug names (needs network; resumable via
# bronze cache). Writes gold/drug_dimension.parquet, which gold-bulk then re-keys against so
# the matrix re-aggregates at ingredient level. ARGS="--limit N" resolves only the busiest N.
drug-dimension:
	$(ENV_PREFIX) PHARMASIGNAL_DATA_ROOT=$(LOCAL_DATA_ROOT) PYTHONPATH=$(PYTHONPATH) $(PY) \
		-m pharmasignal.pipeline.build_drug_dimension $(ARGS)

# Full local backfill: download + ingest FAERS quarterly ZIPs -> silver, then score.
# Skips quarters that are already in silver (idempotent / resumable).
# Prunes each bronze ZIP after ingest so peak extra disk is ~one quarter (~400 MB).
# Default range 2012q4..2025q4; override with QUARTERS= e.g. make backfill-local QUARTERS="2020q1..2025q4"
# DuckDB memory/thread limits are set inside build_gold_bulk; override via env vars.
BACKFILL_QUARTERS ?= 2012q4..2026q1
backfill-local:
	@echo "=== PharmaSignal full-FAERS local backfill ==="
	@echo "    Range: $(BACKFILL_QUARTERS)"
	@echo "    Already-ingested quarters will be skipped automatically."
	@echo "    Bronze ZIPs deleted after each quarter to keep disk footprint minimal."
	@echo ""
	$(ENV_PREFIX) PHARMASIGNAL_DATA_ROOT=$(LOCAL_DATA_ROOT) PYTHONPATH=$(PYTHONPATH) $(PY) \
		-m pharmasignal.ingestion.faers_quarterly --prune-bronze $(BACKFILL_QUARTERS)
	@echo ""
	@echo "=== Scoring full drug x event matrix from silver ==="
	$(ENV_PREFIX) PHARMASIGNAL_DATA_ROOT=$(LOCAL_DATA_ROOT) PYTHONPATH=$(PYTHONPATH) $(PY) \
		-m pharmasignal.pipeline.build_gold_bulk

# --- Spark backfill path (heavy compute) ---------------------------------------
# stage-faers writes raw ASCII to bronze (local or s3:// via PHARMASIGNAL_DATA_ROOT);
# needs internet so run it locally / in CI, NOT on EMR. Then run the Spark jobs.
stage-faers:
	$(ENV_PREFIX) PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.ingestion.stage_faers $(QUARTERS)

# Same PySpark jobs EMR Serverless runs, in local Spark (needs pyspark + Java).
spark-ingest-local:
	$(ENV_PREFIX) PYTHONPATH=$(PYTHONPATH) $(PY) spark/jobs/ingest_faers_spark.py \
		--data-root $(or $(DATA_ROOT),./data) --quarters $(QUARTERS)

spark-gold-local:
	$(ENV_PREFIX) PYTHONPATH=$(PYTHONPATH) $(PY) spark/jobs/build_gold_spark.py \
		--data-root $(or $(DATA_ROOT),./data)

nhanes:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.nhanes.ingest

pubmed:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pubmed.build_evidence

# Reads + rewrites LOCAL gold (signal_scores gets label_status/novel_flag folded in), so
# pin the local root like gold-bulk — then sync to S3 with `make sync-aws`.
labels:
	$(ENV_PREFIX) PHARMASIGNAL_DATA_ROOT=$(LOCAL_DATA_ROOT) PYTHONPATH=$(PYTHONPATH) $(PY) \
		-m pharmasignal.pipeline.build_label_flags

subgroups:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pipeline.build_subgroups

interactions:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pipeline.build_interactions

enrich:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pipeline.enrich_signals

# Full pipeline in dependency order.
pipeline-full: pipeline labels subgroups interactions nhanes pubmed enrich

dashboard:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m streamlit run dashboard/app.py

dashboard-aws:
	$(ENV_PREFIX) PYTHONPATH=$(PYTHONPATH) $(PY) -m streamlit run dashboard/app.py

# Serving API (FastAPI). Reads gold from .env's PHARMASIGNAL_DATA_ROOT (local or s3://).
api-local:
	$(ENV_PREFIX) PYTHONPATH=$(PYTHONPATH) $(PY) -m uvicorn pharmasignal.api.main:app --reload --port 8000

# Push LOCAL data/gold/*.parquet to S3 + re-register Athena (backs up existing gold/ first).
# Needs AWS creds in env (AWS_ACCESS_KEY_ID/SECRET, or `aws configure`). Override the bucket:
#   make sync-aws BUCKET=pharmasignal-data-XXXX REGION=us-east-1
BUCKET ?= pharmasignal-data-762032552349
REGION ?= us-east-1
sync-aws:
	$(ENV_PREFIX) PYTHONPATH=$(PYTHONPATH) $(PY) infrastructure/aws_deploy.py upload   --bucket $(BUCKET) --region $(REGION)
	$(ENV_PREFIX) PYTHONPATH=$(PYTHONPATH) $(PY) infrastructure/aws_deploy.py register --bucket $(BUCKET) --region $(REGION)

# Deploy to AWS: make api-deploy BUCKET=pharmasignal-data-XXXX CORS=https://your-app.vercel.app
api-deploy:
	$(ENV_PREFIX) $(PY) infrastructure/api_deploy.py deploy --bucket $(BUCKET) --cors-origins "$(or $(CORS),*)"

api-url:
	$(ENV_PREFIX) $(PY) infrastructure/api_deploy.py url

test:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pytest -q

clean:
	rm -rf data/bronze/* data/silver/* data/gold/*
	@echo "Cleaned lakehouse data (sample_data/ untouched)."
