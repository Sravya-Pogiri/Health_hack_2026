# Submission form answers (draft)

Fill in yourselves: team name, team lead name and phone, backup contact name and phone, all members with badge
participant numbers and phones, the GitHub link, and the YouTube link.

## 1. Track

Sevaro, Challenge 2: Tele-Neurology Operations Command Center: From Visibility to Intervention.

## 2. What challenge did you try to solve? (text description of the solution)

Sevaro's live view shows who looks busy, but fixing a gap still depends on someone noticing, working out by hand who is
credentialed and eligible, and calling them while a stroke patient waits. Synapp closes that loop. It rebuilds the true
state of the network from the Synapse event stream in the order things happened, finds what needs attention now and in
the next 90 minutes, and ranks it with a plain reason. For the top problem it recommends the least disruptive fix that
works, checks every off-duty doctor against every split-shift rule, and shows why each one is or is not eligible. The
clinical lead approves in one step, the doctor gets a short offer on their phone with no patient details, the first
acceptance wins, coverage updates, and every step is logged. New event files load during the demo with no restart.

## 3. Overview of the project: what we did, how, and why

**What.** Synapp is an add-on to Sevaro's Synapse platform with three simple views: a coordinator's ranked action queue,
a clinical lead's approval screen, and a doctor's phone offer. On the kit data at 6:20 PM it finds a stroke consult at
Oak Valley that nobody approved there can take, a Harbor stroke consult assigned to a doctor who just went unavailable,
a surge at Suncrest (5 consults against a normal 1), and coverage gaps when shifts end at 8 and 9 PM. Of six off-duty
doctors it finds the one who meets every rule, Dr. Okafor, and one offer from 6:35 to 10:00 PM covers Oak Valley,
Suncrest, and the 8 PM Northfield gap.

**How.** A pure-Python engine rebuilds everything from scratch on every change (about 1,200 events in milliseconds), so
late records, duplicates, and appended files are handled the same way. Replaying the kit reproduces Sevaro's reference
snapshot exactly. Availability is inferred from calls, video, camera, and the desktop agent, never from self-reported
status alone. Each problem card answers what, where, why it matters, and what to do, with who approves. Fixes come from
a ladder: reassign, wait for a doctor about to free up, interrupt only lower-priority work, ask a scheduled doctor to
start early, offer a split shift, escalate. A routing model of Sevaro's five rules reproduces all four routing failures
in the kit, and a flood guard keeps a newly joined doctor from being sent every call at every hospital.

**Why.** Time is brain. Sevaro told us the hard parts are having someone credentialed and available to take the call,
prioritizing in real time, and respecting patient privacy, and that simple beats busy. So Synapp ranks instead of
displaying, explains every decision, keeps a person in charge of every message to an off-duty doctor, and stores no
patient information at all.

**Beyond the brief.** A replay scoreboard reruns the day every 5 minutes on what had arrived by then ("Synapp would
have flagged the Suncrest surge at 5:55 PM, 25 minutes before 6:20 PM"). Synapp also shows which credentialing changes
would have prevented today's problems, builds handoff cards for doctors leaving with open consults, and estimates the
value of acting versus not acting. It never ranks doctors by cost.

## 4. Cloud architecture and tools

- **Amazon S3** (versioned, KMS encrypted): event data lake with `kit/`, `raw/` (every events file ever added, never
  overwritten), and `processed/`; `raw/` moves to S3 Glacier after 30 days.
- **AWS Lambda `relay-ingest`**: triggered when a CSV lands in `raw/`; validates the columns, writes a manifest, and
  reloads the engine. A file dropped straight into the bucket updates the dashboard.
- **AWS App Runner** with the image in **Amazon ECR**: runs the FastAPI engine and the React web app over HTTPS.
- **Amazon DynamoDB**: offers, approvals and replies, plus an append-only audit table. A conditional write makes
  "first acceptance wins" safe.
- **Amazon EventBridge Scheduler** and **AWS Lambda `relay-tick`**: a timer every minute for offer expiry, backup
  approver, no-shows, auto-escalation, and stale data.
- **Amazon SNS**: delivers the privacy-guarded offer text and internal alerts.
- **Amazon CloudWatch**: logs, custom metrics, a dashboard, and an alarm when no events arrive for 10 minutes.
- **Amazon Bedrock**: a two-line summary for the clinical lead from structured, non-patient fields; template fallback.
- **AWS KMS, AWS Secrets Manager, AWS IAM, AWS CloudTrail**: encryption, secrets out of code, least privilege, audit.
- **AWS CDK (Python)**: all of the above as code, deployed with one command.
- Application: Python 3.13, FastAPI, React with Vite and TypeScript, pytest (83 tests), Docker.
- Sponsor tools: Microsoft Teams webhook and Outlook calendar invites, ElevenLabs text to speech.

## 5. Installation and deployment steps

Local (no AWS needed):

1. Install Python 3.11 or later and Node.js 18 or later.
2. `git clone <repository URL>` and `cd` into it.
3. `pip install -r requirements-dev.txt`
4. `cd frontend && npm install && npm run build && cd ..`
5. `uvicorn api.main:app --port 8080`, then open http://localhost:8080
6. Optional: `python -m pytest` to run the tests.

Or with Docker: `docker compose up --build`, then open http://localhost:8080.

AWS:

1. Install and sign in to the AWS CLI, install Docker (running), and run `npm install -g aws-cdk`.
2. `cd infra && pip install -r requirements.txt`
3. `cdk bootstrap` (once per account and region).
4. `cdk deploy -c alert_email=you@example.com` (optional: `-c bedrock_model=...`, `-c teams_webhook=...`,
   `-c elevenlabs_secret_arn=...`).
5. Confirm the SNS email subscription and open the `ServiceUrl` output.
6. Live test: upload a CSV on the page, or `aws s3 cp new_events.csv s3://<BucketName>/raw/`.
