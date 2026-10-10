"""Writes frontend mock JSON from the real engine at the kit's "now" (6:20 PM ET).

Files: state.json (start), state_pending.json (a plan waiting for approval), offer.json (the phone view of a sent
offer), state_accepted.json (after the doctor accepts), issue_detail.json, flood_guard.json.

Usage: python scripts/make_mocks.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.notify import Notifier  # noqa: E402
from engine.service import RelayService  # noqa: E402
from engine.storage import LocalStorage  # noqa: E402


def write(out: Path, name: str, data) -> None:
    (out / name).write_text(json.dumps(data, indent=2) + "\n")
    print(f"Wrote {out / name}")


def main() -> None:
    out = ROOT / "frontend" / "src" / "mocks"
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        svc = RelayService(storage=LocalStorage(tmp), notifier=Notifier())
        write(out, "state.json", svc.state())
        top = svc.state()["issues"][0]["primary_issue_id"]
        write(out, "issue_detail.json", svc.issue_detail("iss-unassigned-CON-2105"))
        write(out, "flood_guard.json", svc.simulate())
        plan = svc.propose(issue_id="iss-unassigned-CON-2105")
        write(out, "state_pending.json", svc.state())
        plan = svc.approve(plan["id"], "Dr. Lead (demo)")
        offer_id = plan["offers"][0]["id"]
        write(out, "offer.json", svc.offer_view(offer_id))
        svc.accept(offer_id)
        write(out, "state_accepted.json", svc.state())
        print(f"Top issue at 6:20 PM: {top}")


if __name__ == "__main__":
    main()
