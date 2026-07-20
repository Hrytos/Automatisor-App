"""Create GitHub Actions OIDC role for production backend deploys."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

REGION = "us-east-1"
ACCOUNT = "463470945336"
ROLE_NAME = "github-actions-automatisor-production"
ROOT = Path(__file__).resolve().parents[3]
TRUST = ROOT / "infra" / "production" / "github-actions" / "trust.json"
PERMS = ROOT / "infra" / "production" / "github-actions" / "perms.json"


def aws(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["aws", *args, "--region", REGION]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def main() -> int:
    exists = aws("iam", "get-role", "--role-name", ROLE_NAME, check=False)
    if exists.returncode != 0:
        aws(
            "iam",
            "create-role",
            "--role-name",
            ROLE_NAME,
            "--assume-role-policy-document",
            f"file://{TRUST}",
        )
        print("CREATED_ROLE", ROLE_NAME)
        time.sleep(5)
    else:
        aws(
            "iam",
            "update-assume-role-policy",
            "--role-name",
            ROLE_NAME,
            "--policy-document",
            f"file://{TRUST}",
        )
        print("UPDATED_TRUST", ROLE_NAME)

    aws(
        "iam",
        "put-role-policy",
        "--role-name",
        ROLE_NAME,
        "--policy-name",
        "GithubActionsEcrEcsProduction",
        "--policy-document",
        f"file://{PERMS}",
    )
    arn = json.loads(
        aws("iam", "get-role", "--role-name", ROLE_NAME, "--output", "json").stdout
    )["Role"]["Arn"]
    print("ROLE_ARN", arn)
    print("SET_GITHUB_VAR AWS_ROLE_ARN_PRODUCTION=", arn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
