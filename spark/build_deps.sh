#!/usr/bin/env bash
# Build a portable Python venv for EMR Serverless containing the job dependencies
# (scipy/pandas/numpy/pyarrow/PyYAML/fsspec/s3fs) + the pharmasignal package + the
# YAML config. Built inside Amazon Linux 2023 so the compiled wheels (scipy!) match the
# EMR runtime. PySpark itself is provided by EMR and is intentionally NOT bundled.
#
# Output: build/pharmasignal_deps.tar.gz  (uploaded by infrastructure/spark_backfill.py)
#
# Requires Docker. Run from anywhere:  bash spark/build_deps.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
mkdir -p build

docker run --rm -v "$REPO":/work -w /work \
  public.ecr.aws/amazonlinux/amazonlinux:2023 bash -c '
    set -euo pipefail
    dnf install -y python3.11 python3.11-pip gcc gcc-c++ tar gzip >/dev/null
    python3.11 -m venv /tmp/venv
    source /tmp/venv/bin/activate
    pip install --quiet --upgrade pip venv-pack
    pip install --quiet pandas pyarrow numpy scipy PyYAML fsspec s3fs requests
    pip install --quiet .            # installs the pharmasignal package (src layout)
    # Bundle the YAML config so the driver can find it (PHARMASIGNAL_CONFIG_DIR).
    cp -r config /tmp/venv/pharmasignal_config
    venv-pack -q -o build/pharmasignal_deps.tar.gz
  '

echo "✅ wrote build/pharmasignal_deps.tar.gz ($(du -h build/pharmasignal_deps.tar.gz | cut -f1))"
