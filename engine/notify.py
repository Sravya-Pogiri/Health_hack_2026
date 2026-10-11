"""Message delivery. Local mode keeps an outbox; AWS mode publishes to Amazon SNS topics.

Offers go only to the physician. Internal alerts (coordinator, clinical lead) are low risk and automatic.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("relay.notify")


class Notifier:
    def __init__(self):
        self.outbox: list[dict] = []

    def _record(self, kind: str, **fields) -> dict:
        item = {"kind": kind, "sent_wall_time": datetime.now(timezone.utc).isoformat(), **fields}
        self.outbox.append(item)
        return item

    def owns(self, phone: str, physician_id: str) -> bool:
        """Simulated mode has no phone numbers: any sender may answer (this is how local replies are tested)."""
        return True

    def offer(self, physician_id: str, channel: str, text: str, offer_id: str, name: str = "") -> dict:
        self._record("offer", physician_id=physician_id, channel=channel, text=text, offer_id=offer_id)
        log.info("offer %s to %s by %s: %s", offer_id, physician_id, channel, text)
        return {"channel": channel, "status": "simulated", "label":
                "sent by app notification" if channel == "app_push" else "sent by text message"}

    def internal(self, text: str, audience: str) -> dict:
        log.info("internal alert to %s: %s", audience, text)
        return self._record("internal", audience=audience, text=text)


class SnsNotifier(Notifier):
    """Publishes the same guarded text to SNS. The in-app phone view stays the primary demo path."""

    def __init__(self, offers_topic_arn: str | None, alerts_topic_arn: str | None, teams_webhook: str | None = None):
        super().__init__()
        import boto3

        self.sns = boto3.client("sns")
        self.offers_topic = offers_topic_arn
        self.alerts_topic = alerts_topic_arn
        self.teams_webhook = teams_webhook

    def offer(self, physician_id, channel, text, offer_id, name=""):
        result = super().offer(physician_id, channel, text, offer_id, name)
        if self.offers_topic:
            try:
                self.sns.publish(TopicArn=self.offers_topic, Message=text, Subject="Sevaro coverage request",
                                 MessageAttributes={"physician_id": {"DataType": "String", "StringValue": physician_id},
                                                    "channel": {"DataType": "String", "StringValue": channel}})
                result["status"] = "published"
            except Exception as exc:  # delivery problems must never break the workflow
                log.warning("SNS offer publish failed: %s", exc)
                result["status"] = "publish_failed"
        return result

    def internal(self, text, audience):
        item = super().internal(text, audience)
        if self.alerts_topic:
            try:
                self.sns.publish(TopicArn=self.alerts_topic, Message=text, Subject=f"Relay alert for {audience}")
            except Exception as exc:
                log.warning("SNS alert publish failed: %s", exc)
        if self.teams_webhook:
            _post_teams(self.teams_webhook, text)
        return item


def _post_teams(url: str, text: str) -> None:
    """Microsoft Teams Workflows webhook (best effort)."""
    import urllib.request

    body = json.dumps({"text": text}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, body, {"Content-Type": "application/json"}), timeout=3)
    except Exception as exc:
        log.warning("Teams webhook failed: %s", exc)


# ---------------------------------------------------------------------------------------------
# Twilio: real text messages to the doctor. The reply "1" or "2" comes back through the webhook.
# ---------------------------------------------------------------------------------------------

SMS_SUFFIX = " Reply 1 to accept or 2 to decline."
ACCEPT_WORDS = {"1", "y", "yes", "accept", "ok", "okay", "confirm"}
DECLINE_WORDS = {"2", "n", "no", "decline"}
ONLINE_WORDS = {"online", "here", "logged in"}


def parse_reply(body: str) -> str | None:
    """'1', 'Yes.', 'ACCEPT' -> accept; '2', 'no' -> decline; 'online' -> online; anything else -> None."""
    word = re.sub(r"[^a-z0-9 ]", "", (body or "").lower()).strip()
    if word in ACCEPT_WORDS:
        return "accept"
    if word in DECLINE_WORDS:
        return "decline"
    if word in ONLINE_WORDS:
        return "online"
    return None


def _digits(number: str) -> str:
    return re.sub(r"\D", "", number or "")[-10:]


def twiml(reply: str) -> str:
    from xml.sax.saxutils import escape

    return f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Message>{escape(reply)}</Message></Response>" \
        if reply else "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response/>"


def load_dotenv(path: Path) -> None:
    """Read KEY=VALUE lines from a local .env (never committed). Real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass
class TwilioConfig:
    account_sid: str
    auth_token: str
    from_number: str = ""
    messaging_service_sid: str = ""
    api_base: str = "https://api.twilio.com"
    public_url: str = ""                                  # where Twilio can reach this app, for webhooks
    demo_phone: str = ""                                  # one phone that stands in for every doctor
    doctor_phones: dict = field(default_factory=dict)     # physician_id -> phone, when doctors have their own

    @classmethod
    def from_env(cls, env=None) -> "TwilioConfig | None":
        env = os.environ if env is None else env
        sid, token = env.get("TWILIO_ACCOUNT_SID", ""), env.get("TWILIO_AUTH_TOKEN", "")
        sender, service = env.get("TWILIO_FROM_NUMBER", ""), env.get("TWILIO_MESSAGING_SERVICE_SID", "")
        if not (sid and token and (sender or service)):
            return None
        try:
            phones = json.loads(env.get("RELAY_DOCTOR_PHONES") or "{}")
        except ValueError:
            log.warning("RELAY_DOCTOR_PHONES is not valid JSON; ignoring it")
            phones = {}
        return cls(sid, token, sender, service, env.get("TWILIO_API_BASE", "https://api.twilio.com").rstrip("/"),
                   env.get("RELAY_PUBLIC_URL", "").rstrip("/"), env.get("RELAY_DEMO_PHONE", ""), dict(phones))


class TwilioNotifier(Notifier):
    """Sends each offer as a text message. Internal alerts and the outbox stay with the wrapped notifier."""

    def __init__(self, base: Notifier, cfg: TwilioConfig, urlopen=urllib.request.urlopen):
        super().__init__()
        self.base = base
        self.cfg = cfg
        self.outbox = base.outbox          # one shared outbox
        self._urlopen = urlopen

    # ---- phone book
    def phone_for(self, physician_id: str) -> str:
        return self.cfg.doctor_phones.get(physician_id) or self.cfg.demo_phone

    def owns(self, phone: str, physician_id: str) -> bool:
        mine = self.phone_for(physician_id)
        return bool(mine) and _digits(mine) == _digits(phone)

    # ---- sending
    def offer(self, physician_id, channel, text, offer_id, name=""):
        self._record("offer", physician_id=physician_id, channel="sms", text=text, offer_id=offer_id)
        to = self.phone_for(physician_id)
        if not to:
            log.warning("offer %s: no phone number for %s", offer_id, physician_id)
            return {"channel": "app_push", "status": "no_phone",
                    "label": "no phone number on file, so the offer is only in the app"}
        shared = physician_id not in self.cfg.doctor_phones
        body = (f"[Demo, for {name}] " if shared and name else "") + text + SMS_SUFFIX
        result = self._send_sms(to, body)
        masked = "***" + _digits(to)[-4:]
        delivery = {"channel": "sms", "to": masked, "label": "sent by text message", "body": body, **result}
        log.info("offer %s to %s by text (%s): %s", offer_id, physician_id, masked, result.get("status"))
        return delivery

    def _send_sms(self, to: str, body: str) -> dict:
        form = {"To": to, "Body": body}
        if self.cfg.messaging_service_sid:
            form["MessagingServiceSid"] = self.cfg.messaging_service_sid
        else:
            form["From"] = self.cfg.from_number
        if self.cfg.public_url:
            form["StatusCallback"] = f"{self.cfg.public_url}/api/twilio/status"
        url = f"{self.cfg.api_base}/2010-04-01/Accounts/{self.cfg.account_sid}/Messages.json"
        auth = base64.b64encode(f"{self.cfg.account_sid}:{self.cfg.auth_token}".encode()).decode()
        req = urllib.request.Request(url, urllib.parse.urlencode(form).encode(), method="POST",
                                     headers={"Authorization": f"Basic {auth}",
                                              "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self._urlopen(req, timeout=8) as resp:
                data = json.load(resp)
            return {"status": data.get("status") or "queued", "twilio_sid": data.get("sid")}
        except urllib.error.HTTPError as exc:      # Twilio refused: bad number, trial limits, and so on
            try:
                err = json.loads(exc.read().decode() or "{}")
            except ValueError:
                err = {}
            return {"status": "failed", "error_code": err.get("code"),
                    "error": err.get("message") or f"Twilio answered HTTP {exc.code}"}
        except Exception as exc:                   # delivery problems must never break the workflow
            log.warning("Twilio send failed: %s", exc)
            return {"status": "failed", "error": "Could not reach Twilio"}

    def internal(self, text, audience):
        return self.base.internal(text, audience)

    # ---- webhook security
    def valid_signature(self, url: str, params: dict, signature: str) -> bool:
        """Twilio signs url + sorted POST params with HMAC-SHA1 and the auth token (see twilio.com/docs/usage/security)."""
        data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
        digest = hmac.new(self.cfg.auth_token.encode(), data.encode(), hashlib.sha1).digest()
        return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


def make_notifier() -> Notifier:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    if os.environ.get("RELAY_ENV", "local") == "aws":
        base: Notifier = SnsNotifier(os.environ.get("RELAY_OFFERS_TOPIC_ARN"), os.environ.get("RELAY_ALERTS_TOPIC_ARN"),
                                     os.environ.get("RELAY_TEAMS_WEBHOOK"))
    else:
        base = Notifier()
    twilio = TwilioConfig.from_env()
    if twilio:
        log.info("Twilio text messages are on")
        return TwilioNotifier(base, twilio)
    return base
