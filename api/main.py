"""Relay API (FastAPI). Run locally: uvicorn api.main:app --reload"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from engine.config import REPO_ROOT
from engine.privacy import PrivacyViolation
from engine.service import RelayService

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("relay.api")

@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.environ.get("RELAY_TICKER", "1" if os.environ.get("RELAY_ENV", "local") == "local" else "0") == "1":
        threading.Thread(target=_ticker, args=(float(os.environ.get("RELAY_TICK_SECONDS", "15")),), daemon=True).start()
    yield


app = FastAPI(title="Relay", description="Tele-neurology operations: detect, decide, act.", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_service: RelayService | None = None


def svc() -> RelayService:
    global _service
    if _service is None:
        _service = RelayService()
    return _service


def set_service(service: RelayService) -> None:
    """Used by tests to inject a service with its own storage."""
    global _service
    _service = service


def _check_secret(secret: str | None) -> None:
    expected = os.environ.get("RELAY_SHARED_SECRET")
    if expected and secret != expected:
        raise HTTPException(status_code=401, detail="Missing or wrong shared secret.")


@app.exception_handler(KeyError)
async def _key_error(_: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": f"Not found: {exc}"})


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError):
    status = 422 if isinstance(exc, PrivacyViolation) else 409
    return JSONResponse(status_code=status, content={"detail": str(exc)})


# ------------------------------------------------------------------ read

@app.get("/api/health")
def health():
    s = svc()
    return {"ok": True, "env": os.environ.get("RELAY_ENV", "local"), "events": len(s.ds.events),
            "now": s.now().isoformat()}


@app.get("/api/state")
def state():
    return svc().state()


@app.get("/api/issues/{issue_id}")
def issue(issue_id: str):
    return svc().issue_detail(issue_id)


@app.post("/api/issues/{issue_id}/ack")
def ack(issue_id: str, body: dict = Body(default={})):
    return svc().acknowledge(issue_id, body.get("by", "coordinator"))


@app.get("/api/eligibility")
def eligibility(gap_id: str | None = None):
    s = svc()
    an = s.analysis()
    names = {h.id: h.name for h in s.ds.ref.hospitals.values()}
    result = an.gap_eligibility.get(gap_id) if gap_id else an.cluster
    if result is None:
        raise HTTPException(404, "No gap to evaluate.")
    return result.to_dict(names)


@app.get("/api/simulate/flood-guard")
def flood_guard(gap_id: str | None = None):
    return svc().simulate(gap_id)


@app.get("/api/audit")
def audit():
    return svc().audit()


# ------------------------------------------------------------------ offers

@app.post("/api/offers/propose")
def propose(body: dict = Body(...)):
    return svc().propose(body.get("issue_id"), body.get("gap_id"), body.get("by", "coordinator"),
                         body.get("mode", "one_at_a_time"))


@app.post("/api/offers/{plan_id}/approve")
def approve(plan_id: str, body: dict = Body(default={})):
    return svc().approve(plan_id, body.get("by", "clinical_lead"))


@app.post("/api/offers/{plan_id}/reject")
def reject(plan_id: str, body: dict = Body(default={})):
    return svc().reject(plan_id, body.get("by", "clinical_lead"), body.get("note", ""))


@app.get("/api/offers/{offer_id}")
def offer(offer_id: str):
    return svc().offer_view(offer_id)


@app.post("/api/offers/{offer_id}/accept")
def accept(offer_id: str):
    return svc().accept(offer_id)


@app.post("/api/offers/{offer_id}/decline")
def decline(offer_id: str):
    return svc().decline(offer_id)


@app.post("/api/offers/{offer_id}/online")
def online(offer_id: str):
    return svc().online(offer_id)


@app.get("/api/physicians/{physician_id}/offers")
def physician_offers(physician_id: str):
    return svc().physician_offers(physician_id)


# ------------------------------------------------------------------ coordinator actions

@app.post("/api/actions/{kind}")
def action(kind: str, body: dict = Body(default={})):
    if kind not in ("reassign", "escalate", "undo", "contact", "reclassify"):
        raise HTTPException(404, f"Unknown action {kind}.")
    return svc().coordinator_action(kind, body, body.get("by", "coordinator"))


# ------------------------------------------------------------------ events and clock

@app.post("/api/events/upload")
async def upload(request: Request, file: UploadFile | None = None):
    if file is not None:
        text = (await file.read()).decode("utf-8-sig")
        name = file.filename or "events.csv"
    else:
        text = (await request.body()).decode("utf-8-sig")
        name = request.headers.get("x-filename", "events.csv")
    if not text.strip():
        raise HTTPException(400, "Empty file.")
    return svc().upload(text, name)


@app.post("/api/events")
def add_event(body: dict = Body(...)):
    return svc().add_event(body)


@app.post("/api/reset")
def reset():
    svc().reset()
    return {"ok": True, "now": svc().now().isoformat()}


@app.post("/api/clock")
def clock(body: dict = Body(...)):
    return svc().clock_cmd(body.get("action", ""), body.get("minutes"), body.get("to"), body.get("speed"))


@app.post("/api/uploads/presign")
def presign(body: dict = Body(default={})):
    """AWS: a presigned S3 PUT URL into raw/, so the ingest Lambda triggers the reload. Local: upload directly."""
    if os.environ.get("RELAY_ENV", "local") != "aws":
        return {"mode": "local", "upload_url": "/api/events/upload", "method": "POST"}
    import boto3
    from datetime import datetime, timezone

    name = "".join(ch for ch in body.get("filename", "events.csv") if ch.isalnum() or ch in "._-") or "events.csv"
    key = f"raw/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}_{name}"
    url = boto3.client("s3").generate_presigned_url(
        "put_object", Params={"Bucket": os.environ["RELAY_BUCKET"], "Key": key, "ContentType": "text/csv"},
        ExpiresIn=300)
    return {"mode": "aws", "upload_url": url, "method": "PUT", "key": key}


@app.post("/api/internal/reload")
def internal_reload(x_relay_secret: str | None = Header(default=None)):
    _check_secret(x_relay_secret)
    return svc().reload_events()


@app.post("/api/timers/tick")
def timers_tick(x_relay_secret: str | None = Header(default=None)):
    _check_secret(x_relay_secret)
    changes = svc().tick()
    return {"ok": True, "changes": len(changes), "now": svc().now().isoformat()}


# ------------------------------------------------------------------ background timer (local mode)

def _ticker(interval: float) -> None:
    while True:
        time.sleep(interval)
        try:
            svc().tick()
        except Exception as exc:  # keep ticking; one bad tick must not stop timers
            log.warning("tick failed: %s", exc)


# ------------------------------------------------------------------ frontend (built React app)

DIST = Path(os.environ.get("RELAY_FRONTEND_DIST", REPO_ROOT / "frontend" / "dist"))


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    target = (DIST / path).resolve()
    if path and target.is_file() and DIST.resolve() in target.parents:
        return FileResponse(target)
    index = DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"detail": "Frontend not built. Run: cd frontend && npm install && npm run build"},
                        status_code=404)
