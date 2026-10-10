"""Offer messages: a fixed template with whitelisted fields, plus a guard that blocks anything else.

No patient information ever leaves Relay in a message: no consult IDs, no event descriptions, no clinical words.
"""
from __future__ import annotations

import re
from datetime import datetime

from .timeutil import ET, fmt_clock

TEMPLATE = ("Sevaro coverage request: {hospitals}, {start} to {end} {tz}. Reason: {reason}. "
            "You were asked because {why}. Please reply by {reply_by}.")
ALLOWED_REASONS = {"projected coverage gap", "coverage gap now", "demand surge"}
WHY_PHRASES = {
    "credentialed": "you are credentialed at {n_word} hospital{s}",
    "rested": "fully rested",
    "fast": "can be online quickly",
}
FORBIDDEN_WORDS = re.compile(
    r"\b(patient|mrn|dob|date of birth|diagnosis|symptom\w*|stroke|ct|cta|mri|imaging|seizure|bleed|"
    r"age \d+|\d+\s*(yo|y/o|year[- ]old))\b", re.IGNORECASE)


class PrivacyViolation(ValueError):
    pass


def _hospital_list(names: list[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def build_offer_message(hospital_names: list[str], start: datetime, end: datetime, reason: str,
                        why_keys: list[str], reply_by: datetime) -> str:
    """Only whitelisted fields go into an offer: hospital names, times, a reason category, and why them."""
    if reason not in ALLOWED_REASONS:
        raise PrivacyViolation(f"Reason '{reason}' is not an allowed category.")
    n = len(hospital_names)
    words = {1: "the", 2: "both", 3: "all three", 4: "all four"}
    parts = []
    for key in why_keys:
        phrase = WHY_PHRASES.get(key)
        if phrase is None:
            raise PrivacyViolation(f"'{key}' is not an allowed reason phrase.")
        parts.append(phrase.format(n_word=words.get(n, f"all {n}"), s="s" if n != 1 else ""))
    why = (", ".join(parts[:-1]) + " and " + parts[-1]) if len(parts) > 1 else (parts[0] if parts else
                                                                                "you are on the coverage roster")
    tz = start.astimezone(ET).strftime("%Z") or "ET"
    return TEMPLATE.format(hospitals=_hospital_list(hospital_names), start=fmt_clock(start), end=fmt_clock(end),
                           tz="Eastern" if tz in ("EDT", "EST") else tz, reason=reason, why=why,
                           reply_by=fmt_clock(reply_by))


def check_message(text: str, consult_ids: set[str], descriptions: set[str]) -> None:
    """Raise PrivacyViolation if the message could carry patient information."""
    tokens = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", text))
    leaked = sorted(tokens & consult_ids)
    if leaked:
        raise PrivacyViolation(f"Message contains consult IDs: {', '.join(leaked)}.")
    lowered = text.lower()
    for d in descriptions:
        if len(d) >= 12 and d.lower() in lowered:
            raise PrivacyViolation("Message contains event description text.")
    match = FORBIDDEN_WORDS.search(text)
    if match:
        raise PrivacyViolation(f"Message contains a clinical word: '{match.group(0)}'.")
