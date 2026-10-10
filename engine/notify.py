"""Message delivery. Local mode keeps an outbox; AWS mode publishes to Amazon SNS topics.

Offers go only to the physician. Internal alerts (coordinator, clinical lead) are low risk and automatic.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("relay.notify")


class Notifier:
    def __init__(self):
        self.outbox: list[dict] = []

    def _record(self, kind: str, **fields) -> dict:
        item = {"kind": kind, "sent_wall_time": datetime.now(timezone.utc).isoformat(), **fields}
        self.outbox.append(item)
        return item

    def offer(self, physician_id: str, channel: str, text: str, offer_id: str) -> dict:
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

    def offer(self, physician_id, channel, text, offer_id):
        result = super().offer(physician_id, channel, text, offer_id)
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


def make_notifier() -> Notifier:
    if os.environ.get("RELAY_ENV", "local") == "aws":
        return SnsNotifier(os.environ.get("RELAY_OFFERS_TOPIC_ARN"), os.environ.get("RELAY_ALERTS_TOPIC_ARN"),
                           os.environ.get("RELAY_TEAMS_WEBHOOK"))
    return Notifier()
