"""ElevenLabs text to speech for the guarded offer text (and nothing else). Returns None when not configured."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request

log = logging.getLogger("relay.voice")
_cache: dict[str, bytes] = {}


def speak(text: str) -> bytes | None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return None
    digest = hashlib.sha256(text.encode()).hexdigest()
    if digest in _cache:
        return _cache[digest]
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
        data=json.dumps({"text": text, "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            audio = resp.read()
    except Exception as exc:
        log.warning("ElevenLabs request failed: %s", exc)
        return None
    _cache[digest] = audio
    return audio
