"""Create/update AWS Secrets Manager secret for production backend from local .env.

Does not print secret values. Run with AWS_PROFILE set.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REGION = "us-east-1"
SECRET_NAME = "automatisor/production/backend"
ENV_PATH = Path(
    os.environ.get(
        "AUTOMATISOR_ENV_PATH",
        str(Path(__file__).resolve().parents[3] / ".env"),
    )
)

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


def parse_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        env[key.strip()] = value
    # Frontend Vite name → backend name
    if not env.get("STRIPE_PUBLISHABLE_KEY") and env.get("VITE_STRIPE_PUBLISHABLE_KEY"):
        env["STRIPE_PUBLISHABLE_KEY"] = env["VITE_STRIPE_PUBLISHABLE_KEY"]
    return env


def aws(*args: str) -> str:
    cmd = ["aws", *args, "--region", REGION, "--output", "text"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def main() -> int:
    if not ENV_PATH.exists():
        print(f"MISSING_ENV {ENV_PATH}", file=sys.stderr)
        return 1

    env = parse_env(ENV_PATH)
    payload = {k: env.get(k, "") for k in SECRET_KEYS}

    # Presence / prefix checks only — never print values
    sk = payload.get("STRIPE_SECRET_KEY", "")
    pk = payload.get("STRIPE_PUBLISHABLE_KEY", "")
    print("KEYS", len(SECRET_KEYS))
    print("STRIPE_SECRET_PREFIX", sk[:8] if sk else "MISSING")
    print("STRIPE_PUB_PREFIX", pk[:7] if pk else "MISSING")
    for key in SECRET_KEYS:
        print(f"HAS_{key}", "yes" if payload.get(key) else "empty")

    secret_string = json.dumps(payload)
    tmp = Path(os.environ.get("TEMP", "/tmp")) / "automatisor-production-backend-secret.json"
    tmp.write_text(secret_string, encoding="utf-8")
    try:
        try:
            arn = aws(
                "secretsmanager",
                "describe-secret",
                "--secret-id",
                SECRET_NAME,
                "--query",
                "ARN",
            )
            aws(
                "secretsmanager",
                "put-secret-value",
                "--secret-id",
                SECRET_NAME,
                "--secret-string",
                f"file://{tmp}",
            )
            print("UPDATED", SECRET_NAME)
        except RuntimeError:
            arn = aws(
                "secretsmanager",
                "create-secret",
                "--name",
                SECRET_NAME,
                "--secret-string",
                f"file://{tmp}",
                "--query",
                "ARN",
            )
            print("CREATED", SECRET_NAME)
        print("ARN", arn)
    finally:
        if tmp.exists():
            tmp.unlink()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
