"""Two-line summary for the clinical lead's approval card.

Built from structured, non-patient fields only (hospital names, times, counts, rule results). Amazon Bedrock
writes it when RELAY_BEDROCK_MODEL is set in AWS mode; otherwise a template does. Decisions are always made in
code, never by the model.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("relay.summary")
_cache: dict[str, dict] = {}


def template_summary(fields: dict) -> str:
    who = fields["doctor"]
    if fields["kind"] == "start_early":
        return (f"{who} can start now instead of {fields['scheduled']} and covers {fields['hospitals']}. "
                f"This closes the gap where {fields['problem']} without a split shift.")
    alt = ("the only one who meets every rule" if fields["eligible"] == 1
           else f"the top-ranked of {fields['eligible']} who meet every rule")
    return (f"{fields['problem'][0].upper() + fields['problem'][1:]}. {who} is {alt} of {fields['checked']} off-duty doctors checked, "
            f"and one offer ({fields['window']}) covers {fields['hospitals']}.")


def plan_summary(fields: dict) -> dict:
    key = json.dumps(fields, sort_keys=True)
    if key in _cache:
        return _cache[key]
    model = os.environ.get("RELAY_BEDROCK_MODEL")
    if model and os.environ.get("RELAY_ENV") == "aws":
        try:
            import boto3

            prompt = ("Write two short, plain sentences for a clinical lead approving a coverage offer. No patient "
                      "details. Use only these facts: " + json.dumps(fields))
            resp = boto3.client("bedrock-runtime").converse(
                modelId=model, messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 160, "temperature": 0.2})
            text = resp["output"]["message"]["content"][0]["text"].strip()
            _cache[key] = {"text": text, "source": "bedrock"}
            return _cache[key]
        except Exception as exc:
            log.warning("Bedrock summary failed, using template: %s", exc)
    _cache[key] = {"text": template_summary(fields), "source": "template"}
    return _cache[key]
