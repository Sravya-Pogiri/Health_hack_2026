"""The engine clock. "Now" comes from the data (or a demo control), never the computer clock."""
from __future__ import annotations

import time
from datetime import datetime, timedelta


class Clock:
    def __init__(self):
        self.mode = "data"            # data: follow the latest data; manual: fixed time
        self.manual: datetime | None = None
        self.playing = False
        self.speed = 1.0
        self._anchor_sim: datetime | None = None
        self._anchor_real: float | None = None

    def now(self, data_now: datetime) -> datetime:
        if self.playing and self._anchor_sim is not None:
            return self._anchor_sim + timedelta(seconds=(time.monotonic() - self._anchor_real) * self.speed)
        if self.mode == "manual" and self.manual is not None:
            return self.manual
        return data_now

    def set(self, t: datetime) -> None:
        self.mode, self.manual, self.playing = "manual", t, False

    def advance(self, minutes: float, data_now: datetime) -> None:
        self.set(self.now(data_now) + timedelta(minutes=minutes))

    def play(self, speed: float, data_now: datetime) -> None:
        self._anchor_sim = self.now(data_now)
        self._anchor_real = time.monotonic()
        self.speed, self.playing = max(0.1, float(speed)), True

    def pause(self, data_now: datetime) -> None:
        self.set(self.now(data_now))

    def follow_data(self) -> None:
        self.mode, self.manual, self.playing = "data", None, False

    def after_append(self, data_now: datetime) -> None:
        """New data moves "now" forward, but never backward past a demo clock that is already later."""
        if self.mode == "data" or self.now(data_now) <= data_now:
            self.follow_data()

    def to_dict(self, data_now: datetime) -> dict:
        return {"mode": "playing" if self.playing else self.mode, "speed": self.speed,
                "now": self.now(data_now).isoformat(), "data_now": data_now.isoformat()}
