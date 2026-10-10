"""Storage: uploaded event files, the actions store (offers), and the audit log.

RELAY_ENV=local uses a folder; RELAY_ENV=aws uses Amazon S3 (event data lake) and Amazon DynamoDB
(actions and append-only audit). The engine is the same either way.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


class LocalStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.uploads = self.root / "raw"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # Event files ----------------------------------------------------
    def save_upload(self, name: str, text: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)[:80] or "events.csv"
        path = self.uploads / f"{stamp}_{safe}"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def list_uploads(self) -> list[tuple[str, str]]:
        return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(self.uploads.glob("*.csv"))]

    def clear_uploads(self) -> None:
        for p in self.uploads.glob("*.csv"):
            p.unlink()

    # Actions and audit ----------------------------------------------
    def save_actions(self, data: dict) -> None:
        with self._lock:
            (self.root / "actions.json").write_text(json.dumps(data, indent=1), encoding="utf-8")

    def load_actions(self) -> dict:
        p = self.root / "actions.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def append_audit(self, entry: dict) -> None:
        with self._lock, open(self.root / "audit.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")

    def load_audit(self) -> list[dict]:
        p = self.root / "audit.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def reset_actions(self) -> None:
        for name in ("actions.json", "audit.jsonl"):
            p = self.root / name
            if p.exists():
                p.unlink()


class AwsStorage:
    """S3 bucket with raw/ (every events file ever added) and processed/; DynamoDB actions and audit tables."""

    def __init__(self, bucket: str, actions_table: str, audit_table: str):
        import boto3

        self.s3 = boto3.client("s3")
        self.bucket = bucket
        ddb = boto3.resource("dynamodb")
        self.actions = ddb.Table(actions_table)
        self.audit = ddb.Table(audit_table)
        self.client = boto3.client("dynamodb")
        self.actions_table_name = actions_table

    def save_upload(self, name: str, text: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        key = f"raw/{stamp}_{name}"
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=text.encode(), ContentType="text/csv",
                           ServerSideEncryption="aws:kms")
        return f"s3://{self.bucket}/{key}"

    def list_uploads(self) -> list[tuple[str, str]]:
        out, token = [], None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": "raw/"}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.s3.list_objects_v2(**kwargs)
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(".csv"):
                    body = self.s3.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read().decode()
                    out.append((obj["Key"].split("/", 1)[1], body))
            if not page.get("IsTruncated"):
                break
            token = page["NextContinuationToken"]
        return sorted(out)

    def clear_uploads(self) -> None:
        # Raw files are never deleted (versioned data lake); a reset starts a new demo "day" prefix instead.
        for name, _ in self.list_uploads():
            self.s3.copy_object(Bucket=self.bucket, CopySource={"Bucket": self.bucket, "Key": f"raw/{name}"},
                                Key=f"archive/{name}")
            self.s3.delete_object(Bucket=self.bucket, Key=f"raw/{name}")

    def save_processed(self, text: str) -> None:
        self.s3.put_object(Bucket=self.bucket, Key="processed/event_log.csv", Body=text.encode(),
                           ContentType="text/csv", ServerSideEncryption="aws:kms")

    def save_actions(self, data: dict) -> None:
        self.actions.put_item(Item={"pk": "offer_book", "data": json.dumps(data)})

    def load_actions(self) -> dict:
        item = self.actions.get_item(Key={"pk": "offer_book"}).get("Item")
        return json.loads(item["data"]) if item else {}

    def try_fill_slot(self, plan_id: str, physician_id: str, slots: int) -> bool:
        """Conditional write: two simultaneous acceptances cannot both fill the same slot."""
        try:
            self.client.update_item(
                TableName=self.actions_table_name, Key={"pk": {"S": f"slots#{plan_id}"}},
                UpdateExpression="ADD filled :p",
                ConditionExpression="attribute_not_exists(filled) OR (size(filled) < :n AND NOT contains(filled, :pid))",
                ExpressionAttributeValues={":p": {"SS": [physician_id]}, ":n": {"N": str(slots)},
                                           ":pid": {"S": physician_id}})
            return True
        except self.client.exceptions.ConditionalCheckFailedException:
            return False

    def append_audit(self, entry: dict) -> None:
        self.audit.put_item(Item={"pk": entry.get("plan_id") or "relay", "sk": f"{entry['at']}#{entry['seq']:06d}",
                                  "entry": json.dumps(entry, default=str)})

    def load_audit(self) -> list[dict]:
        items, kwargs = [], {}
        while True:
            page = self.audit.scan(**kwargs)
            items.extend(json.loads(i["entry"]) for i in page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return sorted(items, key=lambda e: e.get("seq", 0))

    def reset_actions(self) -> None:
        self.actions.delete_item(Key={"pk": "offer_book"})
        scan = self.actions.scan(ProjectionExpression="pk")
        for item in scan.get("Items", []):
            if item["pk"].startswith("slots#"):
                self.actions.delete_item(Key={"pk": item["pk"]})


def make_storage(default_root: str | Path) -> LocalStorage | AwsStorage:
    if os.environ.get("RELAY_ENV", "local") == "aws":
        return AwsStorage(os.environ["RELAY_BUCKET"], os.environ.get("RELAY_ACTIONS_TABLE", "relay-actions"),
                          os.environ.get("RELAY_AUDIT_TABLE", "relay-audit"))
    return LocalStorage(os.environ.get("RELAY_DATA_DIR", str(default_root)))
