# PharmaSignal API — AWS Lambda container image.
#
# Why a container instead of a zip: the read path pulls in pandas + pyarrow + duckdb,
# whose binary wheels blow past Lambda's 250 MB *unzipped* zip limit. A container
# image (10 GB limit) ships them cleanly and reuses the existing serving code as-is.
#
# Build context is the REPO ROOT (so we can COPY src/pharmasignal):
#   docker build -f infrastructure/lambda/Dockerfile -t pharmasignal-api .
# Built/pushed/wired automatically by infrastructure/api_deploy.py.

FROM public.ecr.aws/lambda/python:3.12

# Pull current Amazon Linux security fixes before installing Python wheels. This keeps
# base-image RPMs such as libsolv current when AWS publishes patched packages.
RUN dnf -y upgrade --refresh && dnf clean all && rm -rf /var/cache/dnf

# Dependencies first for layer caching.
COPY requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements-api.txt

# Pre-install the DuckDB httpfs extension into a baked directory so the Lambda runtime
# reads gold Parquet directly from S3 (read_parquet('s3://...')) with no network
# extension download on cold start. lakehouse._configure_s3 points at the same dir.
ENV PHARMASIGNAL_DUCKDB_EXT_DIR=/opt/duckdb_ext
RUN python -c "import duckdb; c=duckdb.connect(); c.execute(\"SET extension_directory='/opt/duckdb_ext'; INSTALL httpfs; INSTALL aws;\")"

# The Lambda runtime does not need a package manager after build-time installs.
# Removing it also removes libsolv, which has current ECR scanner findings in
# the Amazon Linux base image until AWS publishes a patched base package.
RUN rpm -e microdnf-dnf microdnf libdnf libsolv librepo

# Application code -> import root as `pharmasignal.*`.
COPY src/pharmasignal ${LAMBDA_TASK_ROOT}/pharmasignal

# Mangum handler exported from the FastAPI module.
CMD ["pharmasignal.api.main.handler"]
