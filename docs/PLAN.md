# Build plan

| Milestone | Target (ET) | Scope |
|---|---|---|
| 1. Engine foundation | Sat 7 PM | Config, loader, event normalization (dedupe, ordering, late events, re-calls), physician and consult state machines, snapshot match test, API contract draft |
| 2. Detect and decide | Sat 11 PM | Standards, surge, coverage now and projected, routing simulator, issues and ranking, intervention ladder, split-shift eligibility |
| 3. Act and API | Sun 3 AM | Offer workflow, privacy guard, append upload, clock controls, flood guard simulation, audit log, FastAPI, AWS infrastructure as code |
| 4. UI | Sun 7 AM | Coordinator, issue drawer, clinical lead, phone view, audit, scoreboard, demo fixtures |
| 5. Standouts and polish | Sun 9 AM | Replay scoreboard, credentialing impact, handoff cards, data health, SNS, CloudWatch, Bedrock |
| 6. Submission | Sun 11 AM | README, ASSUMPTIONS, SUBMISSION, VIDEO_SCRIPT, diagram |

## File structure

```
config.yaml            every threshold
data/kit/              original developer kit files (unchanged)
data/demo_events/      live-test practice files
engine/                pure Python, no web code
  config.py loader.py model.py timeutil.py dataset.py
  events.py            dedupe, ordering, late events, re-calls
  consults.py          consult lifecycle
  physicians.py        activity and availability
  timeline.py          replay at any time t
  snapshot.py          reference snapshot export
  serialize.py         JSON views for the API
  standards.py demand.py coverage.py routing.py issues.py interventions.py
  eligibility.py offers.py insights.py health.py          (later milestones)
api/                   FastAPI app
frontend/              React + Vite + TypeScript
infra/                 AWS CDK (Python)
tests/                 pytest
docs/                  API contract, plan
```
