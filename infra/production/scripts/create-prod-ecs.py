"""Create production ECS Express gateway service with Secrets Manager injection."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REGION = "us-east-1"
SECRET_ID = "automatisor/production/backend"
SERVICE_NAME = "automatisor-backend-prod"
IMAGE = "463470945336.dkr.ecr.us-east-1.amazonaws.com/automatisor-backend:production"

SECRET_KEYS = [
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "RESEND_API_KEY",
    "RESEND_FROM_EMAIL",
    "GOOGLE_MAPS_API_KEY",
    "SLACK_WEBHOOK_URL",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PUBLISHABLE_KEY",
    "SHARE_TOKEN_SECRET",
    "RECOMMENDATION_SYSTEM_URL",
    "RECOMMENDATION_WORKER_SECRET",
    "CRON_SECRET",
    "TRUSTED_AUTH_BYPASS_EMAILS",
    "OPENAI_API_KEY",
]


def aws_json(*args: str):
    cmd = ["aws", *args, "--region", REGION, "--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


def main() -> int:
    secret_arn = aws_json(
        "secretsmanager", "describe-secret", "--secret-id", SECRET_ID
    )["ARN"]
    print("SECRET_ARN", secret_arn)

    payload = {
        "executionRoleArn": "arn:aws:iam::463470945336:role/ecsTaskExecutionRole",
        "infrastructureRoleArn": "arn:aws:iam::463470945336:role/ecsInfrastructureRoleForExpressServices",
        "serviceName": SERVICE_NAME,
        "cluster": "default",
        "healthCheckPath": "/api/health",
        "cpu": "1024",
        "memory": "2048",
        "primaryContainer": {
            "image": IMAGE,
            "containerPort": 3000,
            "environment": [
                {"name": "ENV", "value": "production"},
                {"name": "PORT", "value": "3000"},
                {"name": "COOKIE_SECURE", "value": "true"},
                {"name": "AUTOMATISOR_DRY", "value": "0"},
                {"name": "ALLOW_DEV_TRIGGERS", "value": "0"},
                {"name": "APP_BASE_URL", "value": "https://PENDING_AMPLIFY_URL"},
                {"name": "CORS_ALLOW_ORIGINS", "value": "https://PENDING_AMPLIFY_URL"},
            ],
            "secrets": [
                {"name": key, "valueFrom": f"{secret_arn}:{key}::"}
                for key in SECRET_KEYS
            ],
        },
    }

    tmp = Path(os.environ.get("TEMP", "/tmp")) / "create-express-prod.json"
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    print("INPUT", tmp)

    try:
        out = aws_json(
            "ecs",
            "create-express-gateway-service",
            "--cli-input-json",
            f"file://{tmp}",
        )
    except RuntimeError as exc:
        print("CREATE_FAILED", str(exc)[:800], file=sys.stderr)
        return 1
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    # Print only non-secret summary fields
    svc = out.get("service") or out.get("expressGatewayService") or out
    print("CREATE_OK")
    print(json.dumps(svc, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
