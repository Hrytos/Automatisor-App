"""Create Stripe live webhook for prod ECS and print only non-secret metadata.

Updates Secrets Manager STRIPE_WEBHOOK_SECRET in place; does not print whsec.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REGION = "us-east-1"
SECRET_ID = "automatisor/production/backend"
ECS_WEBHOOK_URL = (
    "https://au-d1ca31a510504feaac32edb0a4bfa0da.ecs.us-east-1.on.aws/api/stripe/webhook"
)
EVENTS = [
    "invoice.payment_succeeded",
    "invoice.payment_failed",
    "setup_intent.succeeded",
    "payment_method.detached",
]
ENV_PATH = Path(
    os.environ.get(
        "AUTOMATISOR_ENV_PATH",
        str(Path(__file__).resolve().parents[3] / ".env"),
    )
)


def parse_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        env[key.strip()] = value
    return env


def aws_json(*args: str):
    cmd = ["aws", *args, "--region", REGION, "--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout) if result.stdout.strip() else {}


def aws_text(*args: str) -> str:
    cmd = ["aws", *args, "--region", REGION, "--output", "text"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def main() -> int:
    try:
        import stripe
    except ImportError:
        print("NEED_STRIPE_PKG", file=sys.stderr)
        return 2

    env = parse_env(ENV_PATH)
    sk = env.get("STRIPE_SECRET_KEY", "")
    if not sk.startswith("sk_live_"):
        print("REFUSING_NON_LIVE_KEY", sk[:8] if sk else "MISSING", file=sys.stderr)
        return 1

    stripe.api_key = sk

    # Avoid duplicate endpoints for same URL
    existing = stripe.WebhookEndpoint.list(limit=100)
    endpoint = None
    for item in existing.auto_paging_iter():
        if item.url == ECS_WEBHOOK_URL:
            endpoint = item
            print("FOUND_EXISTING", endpoint.id, endpoint.status)
            break

    if endpoint is None:
        endpoint = stripe.WebhookEndpoint.create(
            url=ECS_WEBHOOK_URL,
            enabled_events=EVENTS,
            description="AutomatiSOR production ECS",
            api_version=None,
        )
        print("CREATED", endpoint.id)

    secret = getattr(endpoint, "secret", None)
    if not secret:
        print("NO_SECRET_ON_EXISTING_ENDPOINT")
        print("Create a new endpoint or rotate secret in Stripe Dashboard, then re-run.")
        return 3

    # Merge into existing Secrets Manager JSON
    current = json.loads(
        aws_text("secretsmanager", "get-secret-value", "--secret-id", SECRET_ID, "--query", "SecretString")
    )
    current["STRIPE_WEBHOOK_SECRET"] = secret
    tmp = Path(os.environ.get("TEMP", "/tmp")) / "prod-backend-secret-update.json"
    tmp.write_text(json.dumps(current), encoding="utf-8")
    try:
        aws_text(
            "secretsmanager",
            "put-secret-value",
            "--secret-id",
            SECRET_ID,
            "--secret-string",
            f"file://{tmp}",
        )
    finally:
        tmp.unlink(missing_ok=True)

    print("SECRET_UPDATED")
    print("WEBHOOK_URL", ECS_WEBHOOK_URL)
    print("WEBHOOK_ID", endpoint.id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
