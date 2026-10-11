# Deploying Synapp to AWS

Synapp runs the same engine locally and on AWS. Set `RELAY_ENV=aws` and the storage switches from a local folder to
Amazon S3 and Amazon DynamoDB, and messages go through Amazon SNS. The demo never depends on the cloud: if AWS has a
problem, run `docker compose up --build` locally.

## What gets created

| Service | Name | Job |
|---|---|---|
| Amazon S3 (versioned, KMS) | `RelayStack-Events...` | Event data lake: `kit/` (developer kit), `raw/` (every events file ever added), `processed/` (normalized log), `manifests/` (ingest results). `raw/` moves to S3 Glacier after 30 days. |
| AWS Lambda | `relay-ingest` | A CSV lands in `raw/`: check the 8 columns, write a manifest, ask the engine to reload. |
| AWS App Runner (image in Amazon ECR) | `relay` | The FastAPI engine and the React frontend over HTTPS. |
| Amazon DynamoDB | `relay-actions`, `relay-audit` | Offers, approvals, replies; append-only audit log. Conditional writes make "first acceptance wins" safe. |
| Amazon EventBridge Scheduler and AWS Lambda | `relay-timer-tick`, `relay-tick` | Every minute: offer expiry, backup approver, no-shows, auto-escalation, stale-data check. |
| Amazon SNS | `relay-offers`, `relay-coordinator-alerts` | Guarded offer text to a demo phone or email; internal alerts. |
| Amazon CloudWatch | alarm `relay-feed-quiet`, dashboard `relay` | Custom metrics in the `Relay` namespace; alarm when no new events arrive for 10 minutes. |
| AWS KMS, AWS Secrets Manager, AWS IAM | | Encryption at rest, the shared secret for internal calls, least-privilege roles. |

## Steps

1. Install the AWS Command Line Interface (CLI), Node.js 18 or later, Python 3.11 or later, and Docker. Make sure
   Docker is running (the image is built during deploy).
2. Sign in: `aws configure` (or `aws sso login`). Check with `aws sts get-caller-identity`.
3. Install the CDK command line tool: `npm install -g aws-cdk`
4. From the repository root:
   ```bash
   cd infra
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cdk bootstrap            # once per account and region
   cdk deploy -c alert_email=you@example.com -c offer_email=demo-phone@example.com
   ```
5. Confirm the two SNS subscription emails.
6. Open the `ServiceUrl` output.

## Live test: drop a file straight into the bucket

```bash
aws s3 cp ../data/demo_events/01_new_stroke_suncrest.csv s3://<BucketName>/raw/
```

The ingest Lambda validates the file, writes `manifests/01_new_stroke_suncrest.csv.json`, and the dashboard updates
within seconds. Uploading through the web page stores the file in the same place.

## Tear down

`cdk destroy`. The S3 bucket and the audit table are kept on purpose (audit records should outlive the app); delete
them by hand if you want them gone.

## Notes

- App Runner is pinned to one instance because the engine keeps the replayed state in memory. To scale out, each
  instance would rebuild state from S3 and DynamoDB (it already can: `POST /api/internal/reload`), or the state would
  move to a shared cache.
- No patient information is stored anywhere: events carry IDs, times, and operational descriptions only, and offer
  messages are built from a fixed template.
