"""Provision production billing-cron Lambda + DISABLED EventBridge schedule.

Does not print secret values. Schedule starts DISABLED; enable after manual invoke.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REGION = "us-east-1"
ACCOUNT = "463470945336"
FN_NAME = "automatisor-billing-cron-production"
ROLE_NAME = "automatisor-billing-cron-production-role"
SCHEDULER_ROLE = "automatisor-billing-cron-scheduler-production"
SCHEDULE_NAME = "automatisor-billing-cron-production"
SECRET_ID = "automatisor/production/backend"
BILLING_URL = (
    "https://au-d1ca31a510504feaac32edb0a4bfa0da.ecs.us-east-1.on.aws/api/cron/billing"
)
ROOT = Path(__file__).resolve().parents[3]
LAMBDA_SRC = ROOT / "infra" / "billing-cron" / "lambda_function.py"
TRUST = ROOT / "infra" / "billing-cron" / "lambda-trust.json"
SCHED_TRUST = ROOT / "infra" / "billing-cron" / "scheduler-trust.json"
LAMBDA_PERMS = ROOT / "infra" / "production" / "billing-cron" / "lambda-perms.json"
SCHED_PERMS = ROOT / "infra" / "production" / "billing-cron" / "scheduler-perms.json"
SCHEDULE_INPUT = ROOT / "infra" / "production" / "billing-cron" / "schedule-input.json"


def aws(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["aws", *args, "--region", REGION]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def aws_json(*args: str):
    result = aws(*args, "--output", "json")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def aws_text(*args: str) -> str:
    return aws(*args, "--output", "text").stdout.strip()


def ensure_role(name: str, trust_path: Path, perms_path: Path | None = None) -> str:
    arn = f"arn:aws:iam::{ACCOUNT}:role/{name}"
    exists = aws("iam", "get-role", "--role-name", name, check=False)
    if exists.returncode != 0:
        aws(
            "iam",
            "create-role",
            "--role-name",
            name,
            "--assume-role-policy-document",
            f"file://{trust_path}",
        )
        print("CREATED_ROLE", name)
        time.sleep(8)
    else:
        print("ROLE_EXISTS", name)
    if perms_path and perms_path.exists():
        aws(
            "iam",
            "put-role-policy",
            "--role-name",
            name,
            "--policy-name",
            f"{name}-inline",
            "--policy-document",
            f"file://{perms_path}",
        )
    return arn


def main() -> int:
    cron_secret = json.loads(
        aws_text(
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            SECRET_ID,
            "--query",
            "SecretString",
        )
    )["CRON_SECRET"]
    if not cron_secret:
        print("MISSING_CRON_SECRET", file=sys.stderr)
        return 1

    lambda_role = ensure_role(ROLE_NAME, TRUST, LAMBDA_PERMS)
    # Also attach basic execution for logs if needed — lambda-perms covers log group
    ensure_role(SCHEDULER_ROLE, SCHED_TRUST, SCHED_PERMS)

    zip_path = Path(os.environ.get("TEMP", "/tmp")) / "billing-cron-production.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(LAMBDA_SRC, arcname="lambda_function.py")

    env_vars = {
        "Variables": {
            "BILLING_CRON_URL": BILLING_URL,
            "CRON_SECRET": cron_secret,
        }
    }
    env_path = Path(os.environ.get("TEMP", "/tmp")) / "billing-cron-prod-env.json"
    env_path.write_text(json.dumps(env_vars), encoding="utf-8")

    exists = aws("lambda", "get-function", "--function-name", FN_NAME, check=False)
    if exists.returncode != 0:
        aws(
            "lambda",
            "create-function",
            "--function-name",
            FN_NAME,
            "--runtime",
            "python3.11",
            "--role",
            lambda_role,
            "--handler",
            "lambda_function.lambda_handler",
            "--timeout",
            "65",
            "--zip-file",
            f"fileb://{zip_path}",
            "--environment",
            f"file://{env_path}",
        )
        print("CREATED_LAMBDA", FN_NAME)
    else:
        aws(
            "lambda",
            "update-function-code",
            "--function-name",
            FN_NAME,
            "--zip-file",
            f"fileb://{zip_path}",
        )
        time.sleep(3)
        aws(
            "lambda",
            "update-function-configuration",
            "--function-name",
            FN_NAME,
            "--timeout",
            "65",
            "--environment",
            f"file://{env_path}",
        )
        print("UPDATED_LAMBDA", FN_NAME)

    env_path.unlink(missing_ok=True)
    zip_path.unlink(missing_ok=True)

    fn_arn = aws_text("lambda", "get-function", "--function-name", FN_NAME, "--query", "Configuration.FunctionArn")

    # Ensure scheduler can invoke
    aws(
        "lambda",
        "add-permission",
        "--function-name",
        FN_NAME,
        "--statement-id",
        "AllowEventBridgeScheduler",
        "--action",
        "lambda:InvokeFunction",
        "--principal",
        "scheduler.amazonaws.com",
        "--source-arn",
        f"arn:aws:scheduler:{REGION}:{ACCOUNT}:schedule/default/{SCHEDULE_NAME}",
        check=False,
    )

    schedule = json.loads(SCHEDULE_INPUT.read_text(encoding="utf-8"))
    # Force DISABLED for safety
    schedule["State"] = "DISABLED"
    schedule_path = Path(os.environ.get("TEMP", "/tmp")) / "schedule-prod.json"
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")

    existing_sched = aws("scheduler", "get-schedule", "--name", SCHEDULE_NAME, check=False)
    if existing_sched.returncode != 0:
        aws("scheduler", "create-schedule", "--cli-input-json", f"file://{schedule_path}")
        print("CREATED_SCHEDULE", SCHEDULE_NAME, "DISABLED")
    else:
        aws(
            "scheduler",
            "update-schedule",
            "--cli-input-json",
            f"file://{schedule_path}",
        )
        print("UPDATED_SCHEDULE", SCHEDULE_NAME, "DISABLED")
    schedule_path.unlink(missing_ok=True)

    print("FUNCTION_ARN", fn_arn)
    print("BILLING_URL", BILLING_URL)
    print("SCHEDULE_STATE DISABLED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
