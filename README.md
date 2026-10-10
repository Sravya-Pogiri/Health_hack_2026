# Relay

Sevaro can already see problems. Relay gets a doctor covering the gap before a stroke patient waits.

Relay is our entry for Rutgers Health Hack 2026, Sevaro Challenge 2: "Tele-Neurology Operations Command Center: From Visibility to Intervention." It rebuilds the live state of a tele-neurology network from Sevaro's event stream, finds what needs attention now and in the next 90 minutes, recommends the fix, and runs the approval and offer loop.

This README is a work in progress. The full version (architecture, screenshots, deployment) comes with the final milestone.

## Status

- Milestone 1 (engine foundation): replay from the event stream, handling late and duplicate events, matches Sevaro's 6:20 PM reference snapshot exactly.

## Run the tests

```bash
pip install -r requirements.txt
python -m pytest
```

## Layout

- `data/kit/`: the original developer kit files, unchanged
- `engine/`: the Relay engine (pure Python, no web code)
- `config.yaml`: every threshold the engine uses
- `ASSUMPTIONS.md`: every interpretation we made, with the reason
- `docs/api_contract.md`: API shapes for the frontend
- `docs/PLAN.md`: milestones and file structure

All data in this repository is synthetic. Relay stores and uses no patient information.
