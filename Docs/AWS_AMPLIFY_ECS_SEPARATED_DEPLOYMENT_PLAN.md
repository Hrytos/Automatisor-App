# AutomatiSOR AWS Deployment Plan

## Amplify Frontend + ECS Express Mode Backend

This document is an implementation plan for migrating the AutomatiSOR application from its combined Vercel deployment to a separated AWS architecture:

- **React/Vite frontend:** AWS Amplify Hosting
- **FastAPI backend:** Amazon ECS Express Mode
- **Container registry:** Amazon ECR
- **Secrets:** AWS Secrets Manager
- **Logs and metrics:** Amazon CloudWatch
- **Scheduled billing:** Amazon EventBridge Scheduler + AWS Lambda
- **Database:** Existing Supabase project
- **Payments:** Existing Stripe account

The frontend must continue using relative `/api/*` paths. Amplify will reverse-proxy those requests to the public ECS backend. This keeps browser requests same-origin and avoids a frontend-wide API URL refactor.

---

## 1. Deployment Outcome

Target request flow:

```text
Browser
  |
  | https://app.automatisor.com
  v
AWS Amplify Hosting
  |-- /assets/* and application routes --> React/Vite static build
  |
  |-- /api/* ---------------------------> ECS Express Mode
                                               |
                                               |-- Supabase
                                               |-- Stripe
                                               |-- Resend
                                               |-- Slack
                                               |-- Recommendation service
```

Expected public endpoints:

```text
https://app.automatisor.com/                 React application
https://app.automatisor.com/api/*            Browser-facing proxied API
https://api.automatisor.com/api/*            Direct backend API, webhooks and scheduled calls
```

The custom domains are optional during initial testing. AWS-generated HTTPS URLs may be used first.

---

## 2. Non-Negotiable Migration Rules

1. Do not delete or disable the existing Vercel deployment until AWS passes all acceptance tests.
2. Do not disable Vercel Cron until the AWS billing schedule has completed a controlled successful run.
3. Do not enable both billing schedules in production for an extended period; duplicate execution could create duplicate billing activity.
4. Do not commit `.env`, AWS credentials, Supabase service-role keys, Stripe secret keys, webhook secrets or other secrets.
5. Do not expose backend secrets through Vite variables or Amplify frontend environment variables.
6. Do not change existing database data or schemas as part of this deployment unless a separate migration is explicitly approved.
7. Keep frontend API calls relative, for example `fetch("/api/workspace/state")`.
8. The Amplify `/api/<*>` reverse-proxy rule must appear before the SPA fallback rule.
9. API responses containing authentication, account, billing or report data must not be cached.
10. Production cookies must remain `Secure`, `HttpOnly` and use the current intentional `SameSite` policy.

---

## 3. Current Repository Assumptions

The plan assumes the repository contains:

```text
Automatisor-App/
|-- frontend/
|   |-- package.json
|   |-- package-lock.json
|   |-- vite.config.js
|   `-- src/
|-- backend/
|   |-- main.py
|   `-- requirements.txt
|-- package.json
|-- pyproject.toml
`-- vercel.json
```

Current application characteristics:

- The frontend is React 18 with Vite.
- The frontend build output is `frontend/dist`.
- The frontend uses React Router and requires an SPA rewrite.
- Frontend requests currently use relative `/api/*` paths.
- Local Vite development proxies `/api` to `http://localhost:3000`.
- The backend is FastAPI and runs with Uvicorn.
- Vercel currently routes `/` to the frontend and `/api` to the backend.
- Vercel currently invokes `/api/cron/billing` daily.

---

## 4. Workstreams

Implement the migration in this order:

1. Prepare and test the backend container.
2. Deploy the frontend to Amplify.
3. Push the backend image to ECR.
4. Deploy the backend to ECS Express Mode.
5. Configure ECS secrets and runtime variables.
6. Configure the Amplify API reverse proxy.
7. Validate authentication, billing and report workflows.
8. Configure the Stripe webhook against AWS.
9. Replace Vercel Cron with EventBridge Scheduler and Lambda.
10. Configure custom domains.
11. Add automatic backend deployment from GitHub.
12. Cut production traffic over and retain a rollback window.

---

## 5. Codebase Changes

### 5.1 Create `Dockerfile.backend`

Create this file at the repository root:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=3000

COPY backend/requirements.txt ./backend/requirements.txt

RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend

EXPOSE 3000

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-3000}"]
```

Requirements:

- The image must contain only the Python backend and its runtime dependencies.
- The image must not contain `.env` files or frontend source.
- Uvicorn must bind to `0.0.0.0`.
- The runtime port must default to `3000` but respect the `PORT` environment variable.

### 5.2 Create or update `.dockerignore`

```text
.git
.github
.env
.env.*
!.env.example

frontend
node_modules

__pycache__
*.pyc
.pytest_cache
.venv
venv

Docs
```

If the repository later adds another Docker deployment that requires frontend source, use a dedicated Docker build context or a separate ignore strategy rather than silently removing required files.

### 5.3 Add a health endpoint

Add a lightweight endpoint to `backend/main.py` before the catch-all SPA route:

```python
@app.get("/api/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy"}
```

Health endpoint requirements:

- Must return HTTP `200` when the FastAPI process is ready.
- Must not query Supabase, Stripe or other external services.
- Must not expose environment variables, credentials or configuration details.
- Must remain fast enough for frequent load-balancer health checks.

### 5.4 Disable caching for API responses

Add middleware in `backend/main.py` if equivalent middleware does not already exist:

```python
@app.middleware("http")
async def prevent_api_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response
```

Do not add this middleware twice. Confirm that `Request` is already imported from FastAPI before adding imports.

### 5.5 Preserve existing API routes

Do not rename or remove existing `/api/*` routes during the deployment migration. In particular, preserve:

```text
/api/health
/api/frontend-config
/api/auth/*
/api/onboarding/*
/api/workspace/*
/api/account-sites
/api/customer-sites/*
/api/pre-assessment/*
/api/credits/*
/api/billing/*
/api/stripe/*
/api/cron/billing
/api/chat/*
```

The Vercel-specific API alias registration may remain temporarily because it is not required for ECS but should not block the migration. Any later cleanup must be handled in a separate, tested change.

### 5.6 Do not refactor the frontend API base

The frontend should continue calling APIs as follows:

```javascript
fetch("/api/auth/check-email", options)
```

Do not convert calls to hardcoded ECS URLs. The Amplify reverse proxy will provide the backend connection.

---

## 6. Local Backend Container Validation

### 6.1 Build

Run from the repository root:

```bash
docker build -f Dockerfile.backend -t automatisor-backend:local .
```

### 6.2 Run

Use a local `.env` file that is excluded from Git:

```bash
docker run --rm --env-file .env -p 3000:3000 automatisor-backend:local
```

### 6.3 Validate

```bash
curl -i http://localhost:3000/api/health
```

Expected result:

```text
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store

{"status":"healthy"}
```

Also test at least one read-only backend endpoint that requires existing configuration. Do not execute billing or other charge-producing actions during the container smoke test.

### 6.4 Local container acceptance criteria

- Image builds without errors.
- Container starts without import errors.
- Uvicorn listens on port `3000`.
- `/api/health` returns `200`.
- Cloud-dependent initialization does not crash the process.
- No secrets appear in image history, container output or committed files.

---

## 7. Amplify Frontend Configuration

### 7.1 Repository configuration

Configure Amplify with:

```text
Repository: Hrytos/Automatisor-App
Branch: main
Monorepo application root: frontend
Build command: npm run build
Build output directory: dist
```

Do not enable Amplify Gen 2 Backend. The custom FastAPI backend will run on ECS.

### 7.2 Build specification

Use the following build configuration if Amplify requires an explicit YAML file:

```yaml
version: 1

applications:
  - appRoot: frontend
    frontend:
      phases:
        preBuild:
          commands:
            - npm ci
        build:
          commands:
            - npm run build
      artifacts:
        baseDirectory: dist
        files:
          - '**/*'
      cache:
        paths:
          - node_modules/**/*
```

### 7.3 Frontend environment variables

Only add browser-safe build variables. The currently known optional value is:

```text
VITE_CLARITY_PROJECT_ID
```

Never add the following to Amplify frontend environment variables:

```text
SUPABASE_SERVICE_ROLE_KEY
STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET
RESEND_API_KEY
SLACK_WEBHOOK_URL
SHARE_TOKEN_SECRET
RECOMMENDATION_WORKER_SECRET
CRON_SECRET
```

### 7.4 Initial SPA rewrite

Before the ECS backend is available, the frontend may temporarily use only the SPA rule:

```json
[
  {
    "source": "</^[^.]+$|\\.(?!(css|gif|ico|jpg|jpeg|js|png|txt|svg|woff|woff2|ttf|map|json|webp)$)([^.]+$)/>",
    "target": "/index.html",
    "status": "200",
    "condition": null
  }
]
```

At this stage, UI routes should load, but API-dependent features are not expected to work until the ECS proxy is configured.

---

## 8. Amazon ECR Configuration

Use the AWS region:

```text
eu-north-1
```

### 8.1 Create the repository

Create a private ECR repository named:

```text
automatisor-backend
```

Enable image scanning on push where available.

### 8.2 Build the production image

```bash
docker build -f Dockerfile.backend -t automatisor-backend:latest .
```

### 8.3 Authenticate Docker

```bash
aws ecr get-login-password --region eu-north-1 \
  | docker login --username AWS --password-stdin \
  AWS_ACCOUNT_ID.dkr.ecr.eu-north-1.amazonaws.com
```

### 8.4 Tag and push

```bash
docker tag automatisor-backend:latest \
  AWS_ACCOUNT_ID.dkr.ecr.eu-north-1.amazonaws.com/automatisor-backend:latest

docker push \
  AWS_ACCOUNT_ID.dkr.ecr.eu-north-1.amazonaws.com/automatisor-backend:latest
```

For later deployments, also tag images using the Git commit SHA. Do not rely exclusively on mutable `latest` tags in production.

---

## 9. AWS Secrets and Runtime Configuration

### 9.1 Sensitive values

Store sensitive values in AWS Secrets Manager and inject them into the ECS container:

```text
SUPABASE_URL
SUPABASE_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY
RESEND_API_KEY
RESEND_FROM_EMAIL
GOOGLE_MAPS_API_KEY
SLACK_WEBHOOK_URL
STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET
STRIPE_PUBLISHABLE_KEY
SHARE_TOKEN_SECRET
RECOMMENDATION_SYSTEM_URL
RECOMMENDATION_WORKER_SECRET
CRON_SECRET
TRUSTED_AUTH_BYPASS_EMAILS
```

Review the codebase and deployment environment for any additional required variables before the first production deployment.

### 9.2 Non-sensitive runtime values

Configure directly in ECS:

```env
ENV=production
PORT=3000
COOKIE_SECURE=true
AUTOMATISOR_DRY=0
ALLOW_DEV_TRIGGERS=0
```

After the Amplify URL is known:

```env
APP_BASE_URL=https://AMPLIFY_GENERATED_DOMAIN
CORS_ALLOW_ORIGINS=https://AMPLIFY_GENERATED_DOMAIN
```

After the custom frontend domain is active:

```env
APP_BASE_URL=https://app.automatisor.com
CORS_ALLOW_ORIGINS=https://app.automatisor.com
```

During a controlled transition where both domains must work:

```env
CORS_ALLOW_ORIGINS=https://AMPLIFY_GENERATED_DOMAIN,https://app.automatisor.com
```

### 9.3 Required IAM permissions

The ECS task execution role must be able to:

- Pull images from the private ECR repository.
- Write container logs to CloudWatch.
- Read the specific Secrets Manager secrets injected into the task.

Restrict secret access to the exact required ARNs where possible.

---

## 10. ECS Express Mode Backend Deployment

### 10.1 Service configuration

Create an ECS Express Mode service using the ECR backend image.

Recommended initial settings:

| Setting | Value |
|---|---|
| Service name | `automatisor-backend` |
| Image | ECR `automatisor-backend` image digest |
| Container port | `3000` |
| Health-check path | `/api/health` |
| CPU | `1 vCPU` |
| Memory | `2 GB` |
| Minimum tasks | `1` |
| Maximum tasks | `3` |
| Access | Public HTTPS |

Create or select:

- An ECS task execution role.
- An ECS Express Mode infrastructure role.

### 10.2 Backend URL

ECS will provide a URL similar to:

```text
https://automatisor-backend-xxxxx.ecs.eu-north-1.on.aws
```

Validate directly:

```bash
curl -i https://automatisor-backend-xxxxx.ecs.eu-north-1.on.aws/api/health
```

### 10.3 ECS deployment acceptance criteria

- Service reaches an active/healthy state.
- At least one task remains running.
- Load balancer health checks pass.
- `/api/health` returns `200` over HTTPS.
- CloudWatch receives Uvicorn access and application logs.
- Container can reach Supabase, Stripe, Resend and the recommendation service.
- Secrets are available to the container without being printed.

---

## 11. Connect Amplify to ECS

### 11.1 Final Amplify rewrite configuration

Replace `ECS_BACKEND_DOMAIN` with the AWS-generated backend hostname or `api.automatisor.com`.

```json
[
  {
    "source": "/api/<*>",
    "target": "https://ECS_BACKEND_DOMAIN/api/<*>",
    "status": "200",
    "condition": null
  },
  {
    "source": "</^[^.]+$|\\.(?!(css|gif|ico|jpg|jpeg|js|png|txt|svg|woff|woff2|ttf|map|json|webp)$)([^.]+$)/>",
    "target": "/index.html",
    "status": "200",
    "condition": null
  }
]
```

Rule order is mandatory:

1. `/api/<*>` reverse proxy.
2. SPA fallback.

If the SPA rule is first, `/api/*` requests may incorrectly return `index.html` with HTTP `200`.

### 11.2 Cookie and cache configuration

Because authentication uses HTTP-only cookies:

- Ensure Amplify proxy/cache settings include cookies for proxied API requests.
- Ensure `/api/*` responses use `Cache-Control: no-store`.
- Confirm `Set-Cookie` from the backend reaches the browser through Amplify.
- Confirm subsequent requests send the cookie back through Amplify.
- Do not cache responses by user email, bearer token or cookie.

### 11.3 Proxy validation

Open the Amplify URL and use browser DevTools.

Request:

```text
GET https://AMPLIFY_DOMAIN/api/health
```

Expected response:

```json
{"status":"healthy"}
```

Failure indicators:

- HTML is returned instead of JSON: rewrite order or target is wrong.
- `404`: ECS route or proxy target is wrong.
- `502`: ECS is unhealthy, unreachable or using the wrong port.
- Authentication works once but not afterward: cookies are not being preserved.
- A user sees another user's data: caching must be disabled immediately.

---

## 12. End-to-End Application Validation

Execute against the Amplify URL, not directly against ECS, unless specifically testing the backend.

### 12.1 Routing

- `/` loads.
- `/auth` loads.
- Direct navigation to `/workspace` loads the React application.
- Refreshing a React Router page does not return `404`.
- Static assets return correct content types.

### 12.2 Authentication

- Work-email validation succeeds.
- OTP request succeeds.
- OTP verification succeeds.
- `Set-Cookie` is present after successful authentication.
- Protected requests retain authentication through the Amplify proxy.
- Logout clears the authentication cookies.
- Expired sessions follow the current refresh or re-authentication behavior.

### 12.3 Workspace and sites

- Existing workspace loads.
- Accounts and sites load correctly.
- A site can be created without duplicate submission.
- Notes and rating operations persist.
- Shared reports and shared chats resolve correctly.

### 12.4 Pre-assessment

- Request confirmation succeeds.
- Billing usage is recorded exactly once.
- Resend notification succeeds.
- Slack notification succeeds.
- Recommendation-system trigger succeeds.

### 12.5 Billing and Stripe

- Frontend receives the Stripe publishable key.
- SetupIntent creation succeeds in Stripe test mode.
- Payment method confirmation succeeds.
- Billing page loads invoices.
- Customer Portal session creation succeeds.
- Invoice payment flow succeeds in test mode.
- No live charge is performed during deployment validation.

### 12.6 Logs

- Requests appear in CloudWatch.
- Exceptions contain sufficient context but no secrets.
- Health checks do not flood application error logs.

---

## 13. Stripe Webhook Migration

### 13.1 Create an AWS webhook endpoint

Initially configure Stripe with the direct ECS endpoint:

```text
https://ECS_BACKEND_DOMAIN/api/stripe/webhook
```

After the backend custom domain is ready:

```text
https://api.automatisor.com/api/stripe/webhook
```

Subscribe only to events the backend intentionally handles, currently including:

```text
invoice.payment_succeeded
invoice.payment_failed
setup_intent.succeeded
payment_method.detached
```

### 13.2 Configure the new signing secret

Stripe will issue a new endpoint signing secret. Store it in Secrets Manager as the ECS value for:

```text
STRIPE_WEBHOOK_SECRET
```

Restart or redeploy the ECS task after changing the secret.

### 13.3 Validate

- Send Stripe test events.
- Confirm Stripe receives `2xx` responses.
- Confirm CloudWatch records the event without signature errors.
- Confirm database updates occur exactly once where applicable.

Keep the Vercel webhook temporarily during migration. Remove it only after traffic has moved and the AWS endpoint is stable. Evaluate idempotency before sending the same live event to both endpoints.

---

## 14. Replace Vercel Cron

Current schedule:

```text
Endpoint: GET /api/cron/billing
Schedule: 01:00 UTC daily
Authentication: Authorization: Bearer CRON_SECRET
```

Target AWS flow:

```text
EventBridge Scheduler
  -> Lambda
     -> GET https://api.automatisor.com/api/cron/billing
        Authorization: Bearer CRON_SECRET
```

### 14.1 Lambda implementation

```python
import json
import os
import urllib.request


def lambda_handler(event, context):
    request = urllib.request.Request(
        os.environ["BILLING_CRON_URL"],
        method="GET",
        headers={
            "Authorization": f"Bearer {os.environ['CRON_SECRET']}"
        },
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read().decode("utf-8")
        status = response.status

    return {
        "statusCode": 200,
        "body": json.dumps({
            "upstream_status": status,
            "response": body,
        }),
    }
```

Lambda configuration:

```text
BILLING_CRON_URL=https://api.automatisor.com/api/cron/billing
CRON_SECRET=<same secret configured in ECS>
```

Store `CRON_SECRET` securely rather than committing it to Lambda source.

### 14.2 Scheduler expression

```text
cron(0 1 * * ? *)
```

Configure UTC as the schedule timezone.

### 14.3 Safe activation procedure

1. Deploy the Lambda.
2. Invoke it manually against a non-production/dry-run-safe environment if available.
3. Verify authorization and CloudWatch logs.
4. Schedule a controlled production run.
5. Confirm one expected billing execution.
6. Disable Vercel Cron immediately after AWS scheduling is confirmed.
7. Confirm the next scheduled run occurs exactly once.

Do not leave both production schedules active.

---

## 15. Custom Domains

Recommended domain split:

```text
app.automatisor.com -> Amplify
api.automatisor.com -> ECS/Application Load Balancer
```

### 15.1 Frontend domain

Configure `app.automatisor.com` in Amplify Hosting and complete its DNS validation.

### 15.2 Backend domain

1. Request an ACM certificate for `api.automatisor.com` in `eu-north-1`.
2. Complete DNS validation.
3. Attach the certificate to the ECS-created Application Load Balancer HTTPS listener.
4. Add an ALB host rule for `api.automatisor.com` targeting the backend target group.
5. Point the DNS record to the ALB.
6. Verify `/api/health` over the custom domain.

### 15.3 Update runtime configuration

```env
APP_BASE_URL=https://app.automatisor.com
CORS_ALLOW_ORIGINS=https://app.automatisor.com
```

Update the Amplify reverse proxy target:

```text
https://api.automatisor.com/api/<*>
```

Update Stripe and billing scheduler URLs to use the backend custom domain.

### 15.4 Google Maps restriction

Because the Google Maps browser key is visible to users by design, restrict it in Google Cloud Console to the exact production and staging frontend origins. Do not rely on key secrecy for browser-side Google Maps usage.

---

## 16. GitHub Deployment Automation

Add CI/CD only after the first manual ECS deployment is healthy.

### 16.1 Desired workflow

```text
Push to main
  -> Amplify automatically builds frontend
  -> GitHub Actions builds backend image
  -> GitHub Actions authenticates to AWS using OIDC
  -> Image receives commit-SHA tag
  -> Image is pushed to ECR
  -> ECS Express Mode is updated to the new image
  -> ECS health checks/canary deployment validate the release
```

### 16.2 Security requirements

- Use GitHub OIDC to assume an AWS IAM role.
- Do not store long-lived AWS access keys in GitHub Secrets.
- Restrict the IAM role to the specific repository, branch, ECR repository and ECS service.
- Tag images with `${{ github.sha }}`.
- Retain enough previous images for rollback.

### 16.3 Backend workflow triggers

The backend workflow may run on changes to:

```yaml
on:
  push:
    branches:
      - main
    paths:
      - "backend/**"
      - "Dockerfile.backend"
      - ".dockerignore"
      - ".github/workflows/deploy-backend.yml"
```

If root dependency files later affect the backend build, add them to the trigger paths.

### 16.4 Amplify behavior

Amplify remains connected to the same repository and branch. It may rebuild on every push unless configured for monorepo diff deployments. This is acceptable initially; optimize build triggers only after the deployment is stable.

---

## 17. Monitoring and Operations

### 17.1 CloudWatch

Configure or verify:

- ECS container logs.
- Lambda billing logs.
- ECS task CPU and memory.
- ALB target health.
- ALB `4xx` and `5xx` rates.
- ECS running task count.
- Deployment failure alarms.

Set a finite CloudWatch log retention period appropriate for the project rather than retaining logs indefinitely by default.

### 17.2 Recommended alarms

- Backend `5xx` rate above an agreed threshold.
- No healthy ECS targets.
- ECS running task count below `1`.
- Lambda billing invocation failure.
- Lambda billing timeout.
- Repeated Stripe webhook failure.

### 17.3 Minimum task count

Start with one task for cost control. Increase the minimum to at least two or three when production availability requirements justify the additional cost.

---

## 18. Production Cutover Checklist

Complete each item in order:

- [ ] `Dockerfile.backend` added and reviewed.
- [ ] `.dockerignore` added and reviewed.
- [ ] `/api/health` added.
- [ ] API no-cache behavior added or verified.
- [ ] Backend container builds locally.
- [ ] Backend container passes local health check.
- [ ] Amplify frontend deploys successfully.
- [ ] Amplify SPA routes refresh correctly.
- [ ] ECR repository created.
- [ ] Backend image pushed to ECR.
- [ ] ECS Express Mode service created.
- [ ] ECS health check passes.
- [ ] ECS secrets configured.
- [ ] ECS runtime variables configured.
- [ ] Amplify `/api` reverse proxy added before SPA fallback.
- [ ] Amplify proxy returns backend JSON.
- [ ] Authentication cookies survive the proxy.
- [ ] Full OTP flow passes.
- [ ] Workspace and site flows pass.
- [ ] Pre-assessment flow passes without duplicate billing writes.
- [ ] Stripe test-mode workflow passes.
- [ ] AWS Stripe webhook passes test events.
- [ ] AWS billing Lambda passes a controlled invocation.
- [ ] EventBridge schedule configured.
- [ ] Custom frontend domain configured.
- [ ] Custom backend domain configured.
- [ ] ECS `APP_BASE_URL` and `CORS_ALLOW_ORIGINS` updated.
- [ ] Amplify proxy target updated to the backend custom domain.
- [ ] Production DNS moved to Amplify.
- [ ] AWS monitored during rollback window.
- [ ] Vercel Cron disabled after AWS schedule validation.
- [ ] Old Stripe webhook removed after AWS validation.
- [ ] Vercel retained for 24-48 hours as rollback.
- [ ] Vercel removed only after explicit approval.

---

## 19. Rollback Plan

### Frontend rollback

- Redeploy the previous successful Amplify build, or
- Move the production DNS record back to the existing Vercel deployment.

### Backend rollback

- Update ECS to the previous known-good ECR image digest.
- Verify `/api/health` and an authenticated read flow.
- If the ECS backend remains unhealthy, change the Amplify `/api` rewrite target back to the existing Vercel backend.

### Billing rollback

- Disable the EventBridge schedule.
- Re-enable Vercel Cron only after confirming AWS will not execute again.
- Review Stripe and database state before manually retrying any failed billing run.

### Stripe webhook rollback

- Keep the Vercel endpoint available during the rollback window.
- Disable the AWS endpoint if signature or processing failures occur.
- Avoid replaying charge-related events without verifying idempotency.

---

## 20. Definition of Done

The migration is complete when:

1. Users load the React application from Amplify.
2. All frontend `/api/*` requests are transparently served by ECS through the Amplify reverse proxy.
3. Authentication cookies work across login, refresh, protected actions and logout.
4. Supabase, Stripe, Resend, Slack and recommendation integrations work from ECS.
5. Stripe webhooks are delivered successfully to AWS.
6. Billing runs exactly once at the intended daily schedule from AWS.
7. CloudWatch contains useful logs and alarms without exposing secrets.
8. The custom frontend and backend domains use valid HTTPS certificates.
9. A GitHub push can deploy frontend and backend changes through their respective pipelines.
10. Vercel is no longer required for production traffic, cron or webhooks.
11. A tested rollback path and previous backend image remain available.

---

## 21. Suggested Implementation Sequence for a Coding Agent

When this document is supplied to a coding agent, instruct it to perform only the repository changes first:

1. Inspect the current code and confirm assumptions.
2. Add `Dockerfile.backend`.
3. Add or safely update `.dockerignore` without deleting unrelated rules.
4. Add `/api/health` in a location that is registered before the SPA catch-all.
5. Add API no-cache middleware only if equivalent logic is absent.
6. Build the backend Docker image.
7. Run the backend container with a local environment file.
8. Verify `/api/health`.
9. Run existing backend tests.
10. Report the exact files changed and any discovered deployment blockers.

The coding agent must not:

- Create or modify AWS resources without explicit authorization.
- Change Supabase data or schemas.
- Rotate production credentials.
- Disable Vercel.
- Change Stripe live-mode configuration.
- Trigger billing.
- Commit or push unless explicitly requested.

AWS console work, secrets entry, webhook changes, scheduler activation and DNS cutover must remain separately approved operational actions.

---

## 22. Official References

- [AWS Amplify redirects and rewrites](https://docs.aws.amazon.com/amplify/latest/userguide/redirect-rewrite-examples.html)
- [AWS Amplify monorepo configuration](https://docs.aws.amazon.com/amplify/latest/userguide/monorepo-configuration.html)
- [Amazon ECS Express Mode overview](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-overview.html)
- [Create an ECS Express Mode service](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-first-run.html)
- [Push Docker images to Amazon ECR](https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html)
- [Pass Secrets Manager values to ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html)
- [Customize ECS Express Mode resources and domains](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-advanced-customization.html)

