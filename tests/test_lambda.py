"""The ingest Lambda validates columns, writes a manifest, and asks the engine to reload."""
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def handler(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("SERVICE_URL", "https://relay.example")
    monkeypatch.setenv("SECRET_ARN", "arn:aws:secretsmanager:us-east-1:000000000000:secret:x")
    spec = importlib.util.spec_from_file_location("relay_lambda", ROOT / "infra" / "lambda" / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class FakeS3:
        def __init__(self):
            self.objects, self.put = {}, {}

        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(self.objects[Key].encode())}

        def put_object(self, Bucket, Key, Body, ContentType):
            self.put[Key] = json.loads(Body)

    mod.s3 = FakeS3()
    mod.calls = []
    monkeypatch.setattr(mod, "_post", lambda path: mod.calls.append(path) or {"ok": True})
    return mod


def _event(key):
    return {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": key}}}]}


def test_valid_file_triggers_reload(handler):
    handler.s3.objects["raw/live.csv"] = (ROOT / "data" / "kit" / "event_stream.csv").read_text()
    out = handler.ingest(_event("raw/live.csv"), None)
    assert out["files"][0]["valid"] and out["files"][0]["rows"] == 1164
    assert handler.calls == ["/api/internal/reload"]
    assert handler.s3.put["manifests/live.csv.json"]["rows"] == 1164


def test_bad_columns_do_not_reload(handler):
    handler.s3.objects["raw/bad.csv"] = "a,b\n1,2\n"
    out = handler.ingest(_event("raw/bad.csv"), None)
    assert not out["files"][0]["valid"] and "event_id" in out["files"][0]["missing_columns"]
    assert handler.calls == []


def test_tick(handler):
    handler.tick({}, None)
    assert handler.calls == ["/api/timers/tick"]
