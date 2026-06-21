# PharmaSignal — common workflows.
# Uses the bundled FDA-AE virtualenv if present, else system python3.
PY ?= $(shell [ -x FDA-AE/bin/python ] && echo FDA-AE/bin/python || echo python3)
PIP ?= $(PY) -m pip
PYTHONPATH ?= src
ENV_FILE ?= .env
ENV_PREFIX = set -a; [ ! -f $(ENV_FILE) ] || . $(ENV_FILE); set +a;

.PHONY: help install install-dev demo pipeline pipeline-aws nhanes pubmed dashboard dashboard-aws test lint clean

help:
	@echo "PharmaSignal make targets:"
	@echo "  install      Install runtime dependencies"
	@echo "  install-dev  Install runtime + dev/test dependencies"
	@echo "  demo         Generate the bundled offline demo dataset into data/gold"
	@echo "  pipeline     Run the openFDA -> silver/gold pipeline (needs network)"
	@echo "  pipeline-aws Run pipeline using .env, e.g. PHARMASIGNAL_DATA_ROOT=s3://bucket"
	@echo "  nhanes       Ingest NHANES population context (needs network, downloads XPT)"
	@echo "  pubmed       Fetch PubMed evidence for top signals (needs network)"
	@echo "  labels       Flag each signal labeled-vs-novel via openFDA Drug Label API"
	@echo "  enrich       Join PubMed+NHANES into emerging_signals, recompute priority"
	@echo "  pipeline-full Run pipeline -> nhanes -> pubmed -> enrich in order"
	@echo "  dashboard    Launch the Streamlit dashboard"
	@echo "  dashboard-aws Launch dashboard using .env, e.g. PHARMASIGNAL_DATA_ROOT=s3://bucket"
	@echo "  test         Run unit tests"
	@echo "  clean        Remove generated lakehouse data (keeps sample_data/)"

install:
	$(PIP) install -r requirements.txt

install-dev:
	$(PIP) install -r requirements-dev.txt

demo:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pipeline.generate_demo

pipeline:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pipeline.build_gold

pipeline-aws:
	$(ENV_PREFIX) PYTHONPATH=$(PYTHONPATH) $(PY) -m pharmasignal.pipeline.build_gold

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

test:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m pytest -q

clean:
	rm -rf data/bronze/* data/silver/* data/gold/*
	@echo "Cleaned lakehouse data (sample_data/ untouched)."
