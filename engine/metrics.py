"""Amazon CloudWatch custom metrics (AWS mode only). Local mode does nothing.

DataAgeMinutes drives the "feed went quiet" alarm. The alert counters feed the alert-overload scorecard.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("relay.metrics")
_client = None


def publish(values: dict[str, float]) -> None:
    global _client
    if os.environ.get("RELAY_ENV", "local") != "aws":
        return
    try:
        if _client is None:
            import boto3

            _client = boto3.client("cloudwatch")
        _client.put_metric_data(Namespace="Relay", MetricData=[
            {"MetricName": name, "Value": float(value), "Unit": "None"} for name, value in values.items()])
    except Exception as exc:  # metrics must never break the engine
        log.warning("CloudWatch put_metric_data failed: %s", exc)
