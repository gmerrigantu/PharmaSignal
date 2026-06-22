"""PharmaSignal serving-API deploy (boto3 + Docker) — the runnable Option-B cloud setup.

Stands up the read-only FastAPI layer (``pharmasignal.api``) as an AWS Lambda **container
image** behind an **HTTP API Gateway**, so the Vercel frontend can fetch JSON over HTTPS
without ever holding AWS credentials. The Lambda assumes a least-privilege role whose only
data access is *read-only* S3 on the lakehouse bucket — no Athena, no write, no keys in the
browser.

  Vercel (Next.js)  --HTTPS-->  API Gateway (HTTP API, CORS)  -->  Lambda (FastAPI)
                                                                     |  IAM role (S3 read)
                                                                     v
                                                          s3://<bucket>/gold/*.parquet

Subcommands:
  deploy    Build+push the image to ECR, create/update the IAM role, Lambda, and HTTP API.
  url       Print the invoke URL (set it as NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL).
  destroy   Delete the API, Lambda, role, and (optionally) the ECR repo.

Usage:
  python infrastructure/api_deploy.py deploy --bucket pharmasignal-data-XXXX \
      --cors-origins https://your-app.vercel.app
  python infrastructure/api_deploy.py url --bucket pharmasignal-data-XXXX

Prerequisites: Docker running locally; AWS creds in the env chain
(AWS_ACCESS_KEY_ID/SECRET/REGION) with permission to manage ECR/Lambda/IAM/apigatewayv2.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = "infrastructure/lambda/Dockerfile"

ECR_REPO = "pharmasignal-api"
FUNCTION_NAME = "pharmasignal-api"
ROLE_NAME = "pharmasignal-api-role"
API_NAME = "pharmasignal-api"
INLINE_POLICY = "pharmasignal-s3-read"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(cmd: list[str]) -> None:
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)


def _account_id(region: str) -> str:
    return boto3.client("sts", region_name=region).get_caller_identity()["Account"]


# --------------------------------------------------------------------------- #
# 1) ECR repo + image build/push
# --------------------------------------------------------------------------- #
def _ensure_ecr(region: str) -> str:
    ecr = boto3.client("ecr", region_name=region)
    try:
        repo = ecr.describe_repositories(repositoryNames=[ECR_REPO])["repositories"][0]
        print(f"[ecr] repository {ECR_REPO} already exists")
        return repo["repositoryUri"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise

    try:
        ecr.create_repository(repositoryName=ECR_REPO,
                              imageScanningConfiguration={"scanOnPush": True})
        print(f"[ecr] created repository {ECR_REPO}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryAlreadyExistsException":
            raise
        print(f"[ecr] repository {ECR_REPO} already exists")
    return ecr.describe_repositories(repositoryNames=[ECR_REPO])["repositories"][0]["repositoryUri"]


def _build_push(region: str, arch: str) -> str:
    account = _account_id(region)
    registry = f"{account}.dkr.ecr.{region}.amazonaws.com"
    repo_uri = _ensure_ecr(region)
    image_uri = f"{repo_uri}:latest"
    platform = "linux/arm64" if arch == "arm64" else "linux/amd64"

    print("[docker] logging in to ECR")
    auth = boto3.client("ecr", region_name=region).get_authorization_token()[
        "authorizationData"
    ][0]["authorizationToken"]
    _, pw = base64.b64decode(auth).decode("utf-8").split(":", 1)
    subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", registry],
                   input=pw, text=True, check=True)

    print(f"[docker] buildx build + push ({platform})")
    _run(["docker", "buildx", "build", "--platform", platform,
          "--provenance=false", "-f", DOCKERFILE, "-t", image_uri, "--push", "."])
    return image_uri


def _ensure_lambda_ecr_policy(region: str) -> None:
    """Allow Lambda to pull the function image from the private ECR repository."""
    ecr = boto3.client("ecr", region_name=region)
    account = _account_id(region)
    repo_arn = f"arn:aws:ecr:{region}:{account}:repository/{ECR_REPO}"
    desired = {
        "Sid": "LambdaECRImageRetrievalPolicy",
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
    }

    policy = {"Version": "2012-10-17", "Statement": []}
    try:
        raw = ecr.get_repository_policy(repositoryName=ECR_REPO)["policyText"]
        policy = json.loads(raw)
    except ClientError as e:
        if e.response["Error"]["Code"] == "AccessDeniedException":
            print("[ecr] cannot read repository policy; replacing with Lambda pull policy")
        elif e.response["Error"]["Code"] != "RepositoryPolicyNotFoundException":
            raise

    statements = [
        stmt for stmt in policy.get("Statement", [])
        if stmt.get("Sid") != desired["Sid"]
    ]
    statements.append(desired)
    policy["Statement"] = statements
    ecr.set_repository_policy(
        repositoryName=ECR_REPO,
        policyText=json.dumps(policy),
    )
    print(f"[ecr] allowed Lambda image pulls from {repo_arn}")


# --------------------------------------------------------------------------- #
# 2) IAM role (least-privilege: CloudWatch logs + read-only S3 on the bucket)
# --------------------------------------------------------------------------- #
def _ensure_role(region: str, bucket: str) -> str:
    iam = boto3.client("iam", region_name=region)
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole"}]}
    try:
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust),
                        Description="PharmaSignal API Lambda execution role")
        print(f"[iam] created role {ROLE_NAME}")
        time.sleep(10)  # let the new role propagate before Lambda uses it
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            print(f"[iam] role {ROLE_NAME} already exists")
        else:
            raise

    iam.attach_role_policy(
        RoleName=ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")

    s3_read = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"],
         "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"]}]}
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName=INLINE_POLICY,
                        PolicyDocument=json.dumps(s3_read))
    print(f"[iam] attached basic-execution + read-only S3 on {bucket}")
    return iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]


# --------------------------------------------------------------------------- #
# 3) Lambda function (container image)
# --------------------------------------------------------------------------- #
def _wait_active(lam, name: str) -> None:
    for _ in range(60):
        cfg = lam.get_function_configuration(FunctionName=name)
        if cfg.get("State") == "Active" and cfg.get("LastUpdateStatus") == "Successful":
            return
        time.sleep(2)
    raise RuntimeError("Lambda did not become Active in time")


def _deploy_lambda(region: str, image_uri: str, role_arn: str, bucket: str,
                   cors: str, arch: str) -> str:
    lam = boto3.client("lambda", region_name=region)
    env = {"Variables": {
        "PHARMASIGNAL_DATA_ROOT": f"s3://{bucket}",
        "PHARMASIGNAL_CORS_ORIGINS": cors,
        "PHARMASIGNAL_CACHE_TTL": "300",
    }}
    architectures = ["arm64" if arch == "arm64" else "x86_64"]
    try:
        lam.get_function(FunctionName=FUNCTION_NAME)
        exists = True
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        exists = False

    if exists:
        lam.update_function_code(FunctionName=FUNCTION_NAME, ImageUri=image_uri,
                                 Architectures=architectures)
        _wait_active(lam, FUNCTION_NAME)
        lam.update_function_configuration(
            FunctionName=FUNCTION_NAME, Role=role_arn, Timeout=30, MemorySize=512,
            Environment=env)
        print(f"[lambda] updated {FUNCTION_NAME}")
    else:
        lam.create_function(
            FunctionName=FUNCTION_NAME, PackageType="Image",
            Code={"ImageUri": image_uri}, Role=role_arn, Architectures=architectures,
            Timeout=60, MemorySize=2048, Environment=env,
            Description="PharmaSignal read-only gold-mart serving API")
        print(f"[lambda] created {FUNCTION_NAME}")
    _wait_active(lam, FUNCTION_NAME)
    return lam.get_function(FunctionName=FUNCTION_NAME)["Configuration"]["FunctionArn"]


# --------------------------------------------------------------------------- #
# 4) HTTP API Gateway (with CORS) -> Lambda proxy
# --------------------------------------------------------------------------- #
def _find_api(api, name: str) -> dict | None:
    for item in api.get_apis().get("Items", []):
        if item["Name"] == name:
            return item
    return None


def _deploy_api(region: str, lambda_arn: str, cors: str) -> str:
    api = boto3.client("apigatewayv2", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    origins = [o.strip() for o in cors.split(",") if o.strip()] or ["*"]
    cors_cfg = {"AllowOrigins": origins, "AllowMethods": ["GET", "OPTIONS"],
                "AllowHeaders": ["*"], "MaxAge": 3600}

    existing = _find_api(api, API_NAME)
    if existing:
        api_id = existing["ApiId"]
        api.update_api(ApiId=api_id, CorsConfiguration=cors_cfg)
        print(f"[apigw] reusing HTTP API {api_id}")
    else:
        created = api.create_api(Name=API_NAME, ProtocolType="HTTP",
                                 CorsConfiguration=cors_cfg)
        api_id = created["ApiId"]
        print(f"[apigw] created HTTP API {api_id}")

    # AWS_PROXY integration (payload v2) -> the Lambda.
    integrations = api.get_integrations(ApiId=api_id).get("Items", [])
    if integrations:
        integ_id = integrations[0]["IntegrationId"]
    else:
        integ_id = api.create_integration(
            ApiId=api_id, IntegrationType="AWS_PROXY", IntegrationUri=lambda_arn,
            PayloadFormatVersion="2.0", IntegrationMethod="POST")["IntegrationId"]

    target = f"integrations/{integ_id}"
    existing_routes = {r["RouteKey"] for r in api.get_routes(ApiId=api_id).get("Items", [])}
    for route_key in ("ANY /", "ANY /{proxy+}"):
        if route_key not in existing_routes:
            api.create_route(ApiId=api_id, RouteKey=route_key, Target=target)

    # $default auto-deploy stage.
    try:
        api.create_stage(ApiId=api_id, StageName="$default", AutoDeploy=True)
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("ConflictException", "BadRequestException"):
            raise

    # Allow API Gateway to invoke the Lambda.
    account = _account_id(region)
    source_arn = f"arn:aws:execute-api:{region}:{account}:{api_id}/*/*"
    try:
        lam.add_permission(FunctionName=FUNCTION_NAME, StatementId="apigw-invoke",
                           Action="lambda:InvokeFunction", Principal="apigateway.amazonaws.com",
                           SourceArn=source_arn)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise

    return api.get_api(ApiId=api_id)["ApiEndpoint"]


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def deploy(args) -> None:
    image_uri = _build_push(args.region, args.arch)
    _ensure_lambda_ecr_policy(args.region)
    role_arn = _ensure_role(args.region, args.bucket)
    lambda_arn = _deploy_lambda(args.region, image_uri, role_arn, args.bucket,
                                args.cors_origins, args.arch)
    endpoint = _deploy_api(args.region, lambda_arn, args.cors_origins)
    print("\n✅ deploy complete.")
    print(f"\nAPI base URL:  {endpoint}")
    print("\nNext steps:")
    print(f"  • Test:   curl {endpoint}/health")
    print("  • Vercel: set env var")
    print(f"            NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL={endpoint}")
    print("  • Re-deploy the frontend so the build picks up the env var.")


def url(args) -> None:
    api = boto3.client("apigatewayv2", region_name=args.region)
    found = _find_api(api, API_NAME)
    if not found:
        print("No PharmaSignal API deployed. Run `deploy` first.")
        sys.exit(1)
    print(found["ApiEndpoint"])


def destroy(args) -> None:
    region = args.region
    api = boto3.client("apigatewayv2", region_name=region)
    found = _find_api(api, API_NAME)
    if found:
        api.delete_api(ApiId=found["ApiId"])
        print(f"[apigw] deleted API {found['ApiId']}")
    lam = boto3.client("lambda", region_name=region)
    try:
        lam.delete_function(FunctionName=FUNCTION_NAME)
        print(f"[lambda] deleted {FUNCTION_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    iam = boto3.client("iam", region_name=region)
    try:
        iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=INLINE_POLICY)
        iam.detach_role_policy(
            RoleName=ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        iam.delete_role(RoleName=ROLE_NAME)
        print(f"[iam] deleted role {ROLE_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
    if args.delete_ecr:
        ecr = boto3.client("ecr", region_name=region)
        try:
            ecr.delete_repository(repositoryName=ECR_REPO, force=True)
            print(f"[ecr] deleted repository {ECR_REPO}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "RepositoryNotFoundException":
                raise
    print("\n✅ destroy complete.")


def main() -> None:
    p = argparse.ArgumentParser(description="Deploy the PharmaSignal serving API to AWS.")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("deploy", help="build+push image, create/update role, Lambda, HTTP API")
    d.add_argument("--bucket", required=True, help="lakehouse S3 bucket (data root)")
    d.add_argument("--cors-origins", default="*",
                   help="comma-separated allowed origins, e.g. https://your-app.vercel.app")
    d.add_argument("--arch", choices=["x86_64", "arm64"], default="x86_64")
    d.set_defaults(func=deploy)

    u = sub.add_parser("url", help="print the deployed API base URL")
    u.set_defaults(func=url)

    x = sub.add_parser("destroy", help="tear down API, Lambda, and role")
    x.add_argument("--delete-ecr", action="store_true", help="also delete the ECR repo")
    x.set_defaults(func=destroy)

    for parser in (u, x):
        parser.add_argument("--bucket", default="", help=argparse.SUPPRESS)
    for parser in (d, u, x):  # --region per-subcommand (parent optionals must precede subcmd)
        parser.add_argument("--region", default="us-east-1")

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
