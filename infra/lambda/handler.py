"""Relay Lambdas.

ingest: an events CSV lands in s3://bucket/raw/ -> check the 8 columns -> write a manifest -> ask the engine to
        reload. A judge can drop a file straight into the bucket and watch the dashboard update.
tick:   called every minute by EventBridge Scheduler -> the engine processes timers on its own clock.
"""
import csv
import io
import json
import os
import urllib.parse
import urllib.request

import boto3

REQUIRED = ["event_id", "event_time", "ingested_at", "consult_id", "event_type", "physician_id", "hospital_id",
            "description"]
s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")
_secret = None


def _shared_secret() -> str:
    global _secret
    if _secret is None:
        _secret = secrets.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"]
    return _secret


def _post(path: str) -> dict:
    req = urllib.request.Request(os.environ["SERVICE_URL"].rstrip("/") + path, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "X-Relay-Secret": _shared_secret()})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode() or "{}")


def ingest(event, context):
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        text = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        columns = [c.strip() for c in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED if c not in columns]
        rows = list(reader) if not missing else []
        bad = sum(1 for r in rows if not (r.get("event_id") and r.get("event_time") and r.get("event_type")))
        manifest = {"key": key, "rows": len(rows), "rows_missing_required_fields": bad, "missing_columns": missing,
                    "valid": not missing}
        if manifest["valid"]:
            try:
                manifest["reload"] = _post("/api/internal/reload")
            except Exception as exc:  # the file stays in raw/; the next reload picks it up
                manifest["reload_error"] = str(exc)
        s3.put_object(Bucket=bucket, Key="manifests/" + key.split("/", 1)[-1] + ".json",
                      Body=json.dumps(manifest).encode(), ContentType="application/json")
        print(json.dumps(manifest))
        results.append(manifest)
    return {"files": results}


def tick(event, context):
    result = _post("/api/timers/tick")
    print(json.dumps(result))
    return result
