"""Update prod ECS Express plain env APP_BASE_URL / CORS after Amplify exists."""
from __future__ import annotations

import json
import os
import subprocess
import sys

REGION = "us-east-1"
SERVICE_ARN = "arn:aws:ecs:us-east-1:463470945336:service/default/automatisor-backend-prod"
SECRET_ID = "automatisor/production/backend"
IMAGE = "463470945336.dkr.ecr.us-east-1.amazonaws.com/automatisor-backend:production"
AMPLIFY_URL = "https://autodeploy-prod.dp5lmur365jh5.amplifyapp.com"

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
    payload = {
        "primaryContainer": {
            "image": IMAGE,
            "containerPort": 3000,
            "environment": [
                {"name": "ENV", "value": "production"},
                {"name": "PORT", "value": "3000"},
                {"name": "COOKIE_SECURE", "value": "true"},
                {"name": "AUTOMATISOR_DRY", "value": "0"},
                {"name": "ALLOW_DEV_TRIGGERS", "value": "0"},
                {"name": "APP_BASE_URL", "value": AMPLIFY_URL},
                {"name": "CORS_ALLOW_ORIGINS", "value": AMPLIFY_URL},
            ],
            "secrets": [
                {"name": key, "valueFrom": f"{secret_arn}:{key}::"}
                for key in SECRET_KEYS
            ],
        }
    }
    path = os.path.join(os.environ.get("TEMP", "/tmp"), "update-express-prod-cors.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload["primaryContainer"], fh)
    aws_json(
        "ecs",
        "update-express-gateway-service",
        "--service-arn",
        SERVICE_ARN,
        "--primary-container",
        f"file://{path}",
    )
    print("UPDATED_APP_BASE_URL", AMPLIFY_URL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
