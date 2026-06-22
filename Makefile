# PharmaSignal — common workflows.
# Uses the bundled FDA-AE virtualenv if present, else system python3.
PY ?= $(shell [ -x FDA-AE/bin/python ] && echo FDA-AE/bin/python || echo python3)
PIP ?= $(PY) -m pip
PYTHONPATH ?= src
ENV_FILE ?= .env
ENV_PREFIX = set -a; [ ! -f $(ENV_FILE) ] || . $(ENV_FILE); set +a;

.PHONY: help install install-dev install-api demo pipeline pipeline-aws ingest-faers gold-bulk stage-faers spark-ingest-local spark-gold-local nhanes pubmed dashboard dashboard-aws api-local api-deploy api-url test lint clean

help:
	@echo "PharmaSignal make targets:"
	@echo "  install      Install runtime dependencies"
	@echo "  install-dev  Install runtime + dev/test dependencies"
	@echo "  demo         Generate the bundled offline demo dataset into data/gold"
	@echo "  pipeline     Run the openFDA -> silver/gold pipeline (API mode, demo scope)"
	@echo "  pipeline-aws Run pipeline using .env, e.g. PHARMASIGNAL_DATA_ROOT=s3://bucket"
	@echo "  ingest-faers Download FAERS quarterly ZIPs -> silver (QUARTERS=\"2023q1..2023q4\")"
	@echo "  gold-bulk    Score the whole drug x event matrix from silver via SQL (no API)"
	@echo "  stage-faers  Download+extract FAERS ASCII -> bronze for Spark (QUARTERS=...)"
	@echo "  spark-ingest-local  Run the PySpark ingest job locally (bronze -> silver)"
	@echo "  spark-gold-local    Run the PySpark gold/score job locally (silver -> gold)"
	@echo "  nhanes       Ingest NHANES population context (needs network, downloads XPT)"
	@echo "  pubmed       Fetch PubMed evidence for top signals (needs network)"
	@echo "  labels       Flag each signal labeled-vs-novel via openFDA Drug Label API"
	@echo "  enrich       Join PubMed+NHANES into emerging_signals, recompute priority"
	@echo "  pipeline-full Run pipeline -> nhanes -> pubmed -> enrich in order"
	@echo "  dashboard    Launch the Streamlit dashboard"
	@echo "  dashboard-aws Launch dashboard using .env, e.g. PHARMASIGNAL_DATA_ROOT=s3://bucket"
	@echo "  api-local    Run the FastAPI serving layer locally (uvicorn :8000)"
	@echo "  api-deploy   Build+push image and deploy Lambda + HTTP API (BUCKET=, CORS=)"
	@echo "  api-url      Print the deployed API base URL"
	@echo "  test         Run unit tests"
	@echo "  clean        Remove generated lakehouse data (keeps sample_data/)"

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

# Set-based SQL scoring over silver -> gold (no network). Run after ingest-faers.
gold-bulk:
	$(ENV_PREFIX) PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pipeline.build_gold_bulk

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

labels:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pipeline.build_label_flags

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
