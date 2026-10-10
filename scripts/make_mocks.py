"""Writes frontend mock JSON from the real engine state at the kit's "now" (18:20 ET).

Usage: python scripts/make_mocks.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.dataset import Dataset  # noqa: E402
from engine.serialize import state_json  # noqa: E402
from engine.timeline import replay  # noqa: E402


def main() -> None:
    ds = Dataset()
    world = replay(ds, ds.default_now())
    out = ROOT / "frontend" / "src" / "mocks"
    out.mkdir(parents=True, exist_ok=True)
    (out / "state.json").write_text(json.dumps(state_json(ds, world), indent=2) + "\n")
    print(f"Wrote {out / 'state.json'}")


if __name__ == "__main__":
    main()
