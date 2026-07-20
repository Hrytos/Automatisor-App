"""Ensure prod ECS Stripe webhook has a signing secret stored in Secrets Manager.

Uses Stripe REST API directly (avoids SDK roll_secret gaps). Never prints whsec.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
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
ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def parse_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def stripe_request(api_key: str, method: str, path: str, form: dict | None = None) -> dict:
    data = None
    headers = {"Authorization": f"Bearer {api_key}"}
    if form is not None:
        from urllib.parse import urlencode

        data = urlencode(form, doseq=True).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(
        f"https://api.stripe.com{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Stripe {method} {path} -> {exc.code}: {body[:400]}") from exc


def aws_text(*args: str) -> str:
    result = subprocess.run(
        ["aws", *args, "--region", REGION, "--output", "text"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def main() -> int:
    env = parse_env(ENV_PATH)
    sk = env.get("STRIPE_SECRET_KEY", "")
    if not sk.startswith("sk_live_"):
        print("REFUSING_NON_LIVE", sk[:8] if sk else "MISSING", file=sys.stderr)
        return 1

    listed = stripe_request(sk, "GET", "/v1/webhook_endpoints?limit=100")
    endpoint = next((e for e in listed.get("data", []) if e.get("url") == ECS_WEBHOOK_URL), None)

    secret = None
    endpoint_id = None
    if endpoint:
        endpoint_id = endpoint["id"]
        try:
            rolled = stripe_request(sk, "POST", f"/v1/webhook_endpoints/{endpoint_id}/roll_secret")
            secret = rolled.get("secret")
            print("ROLLED", endpoint_id)
        except RuntimeError as exc:
            print("ROLL_FAILED", str(exc)[:200])
            stripe_request(sk, "DELETE", f"/v1/webhook_endpoints/{endpoint_id}")
            endpoint = None

    if not endpoint:
        form: dict = {
            "url": ECS_WEBHOOK_URL,
            "description": "AutomatiSOR production ECS",
        }
        for i, event in enumerate(EVENTS):
            form[f"enabled_events[{i}]"] = event
        created = stripe_request(sk, "POST", "/v1/webhook_endpoints", form)
        endpoint_id = created["id"]
        secret = created.get("secret")
        print("CREATED", endpoint_id)

    if not secret or not secret.startswith("whsec_"):
        print("NO_SIGNING_SECRET", file=sys.stderr)
        return 2

    current = json.loads(
        aws_text(
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            SECRET_ID,
            "--query",
            "SecretString",
        )
    )
    current["STRIPE_WEBHOOK_SECRET"] = secret
    tmp = Path(os.environ.get("TEMP", "/tmp")) / "prod-whsec-update.json"
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
    print("WEBHOOK_ID", endpoint_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
