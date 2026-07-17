"""AWS Lambda handler: invoke the backend's daily billing cron endpoint.

Triggered by an EventBridge Scheduler rule (see deployment plan §14). Calls
GET {BILLING_CRON_URL} with `Authorization: Bearer {CRON_SECRET}` and relays
the upstream status/body. No third-party dependencies — stdlib only, so no
packaging/layers are required.

Required environment variables:
  BILLING_CRON_URL  e.g. https://<ecs-domain>/api/cron/billing
  CRON_SECRET       must match the backend's CRON_SECRET
"""

import json
import os
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 60


def lambda_handler(event, context):
    billing_cron_url = os.environ["BILLING_CRON_URL"]
    cron_secret = os.environ["CRON_SECRET"]

    request = urllib.request.Request(
        billing_cron_url,
        method="GET",
        headers={"Authorization": f"Bearer {cron_secret}"},
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            upstream_status = response.status
            upstream_body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        upstream_status = exc.code
        upstream_body = exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return {
            "statusCode": 502,
            "body": json.dumps({"error": "unreachable", "detail": str(exc.reason)}),
        }

    print(f"billing cron upstream_status={upstream_status} body={upstream_body}")

    return {
        "statusCode": 200 if upstream_status < 400 else upstream_status,
        "body": json.dumps({
            "upstream_status": upstream_status,
            "response": upstream_body,
        }),
    }
