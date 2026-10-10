# Relay

Sevaro can already see problems. Relay gets a doctor covering the gap before a stroke patient waits.

Relay is our entry for Rutgers Health Hack 2026, Sevaro Challenge 2: "Tele-Neurology Operations Command Center: From Visibility to Intervention." It rebuilds the live state of a tele-neurology network from Sevaro's event stream, finds what needs attention now and in the next 90 minutes, recommends the fix, and runs the approval and offer loop.

This README is a work in progress. The full version (architecture, screenshots, deployment) comes with the final milestone.

## Status

- Milestone 1 (engine foundation): replay from the event stream, handling late and duplicate events, matches Sevaro's 6:20 PM reference snapshot exactly.
- Milestone 2 (detect and decide): time standards, surge, coverage now and projected, routing simulator, ranked issues with four-question cards, intervention ladder, split-shift eligibility.
- Milestone 3 (act): offer workflow with approval, expiry, two acceptances, retraction, no-shows and audit; privacy guard; live event upload without restart; demo clock; flood guard simulation; FastAPI; AWS infrastructure as code.

## Run it locally

```bash
pip install -r requirements-dev.txt
uvicorn api.main:app --port 8080          # API at http://localhost:8080/api/state
```

Or with Docker (API plus the built frontend): `docker compose up --build`, then open http://localhost:8080.

## Run the tests

```bash
python -m pytest
```

## Deploy to AWS

See `infra/README.md`. One command after setup: `cd infra && cdk deploy`.

## Layout

- `data/kit/`: the original developer kit files, unchanged
- `engine/`: the Relay engine (pure Python, no web code)
- `api/`: the FastAPI app that serves the engine and the frontend
- `infra/`: AWS CDK (Python) stack and the Lambda functions
- `config.yaml`: every threshold the engine uses
- `ASSUMPTIONS.md`: every interpretation we made, with the reason
- `docs/api_contract.md`: API shapes for the frontend
- `docs/PLAN.md`: milestones and file structure

All data in this repository is synthetic. Relay stores and uses no patient information.
