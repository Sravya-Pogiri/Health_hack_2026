# Video script (under 5 minutes)

Record at 1920 x 1080. Before recording: open the app, click **Reset**, and have `data/demo_events/01_new_stroke_suncrest.csv`
ready. Keep the clinical lead and doctor phone views in their own browser tabs.

## 0:00 to 0:40 · The problem

*Screen: coordinator view at 6:20 PM.*

"Time is brain. At 6:20 PM on a busy Tuesday, Sevaro's live view looks fine: almost every doctor says Available. But
look closer. A stroke consult at Oak Valley has failed routing three times, because the only two doctors approved there
are on a video and unavailable. Suncrest has had five consults in an hour when its normal busy level is one. A stroke
consult at Harbor is assigned to a doctor who just went unavailable. And two doctors leave at 7 PM, four more at 8 PM.
Today, someone has to notice all that and fix it by phone."

## 0:40 to 1:10 · Our approach

*Screen: hospital strip, then the doctor list.*

"Synapp is an add-on to Synapse that goes from seeing to acting: detect, decide, act. It rebuilds the real state from
the event stream in the order things happened, so late and duplicate records cannot fool it, and it matches Sevaro's
reference snapshot exactly. It works out who is really free from calls, video, and camera signals, not from
self-reported status. And it never touches patient information."

## 1:10 to 3:30 · Live demo

1. *Point at card 2, Oak Valley.* "Every problem is ranked and answers four questions: what, where, why it matters, and
   what to do, with who approves." Read the why line.
2. *Click Details and why not.* "Here is why not the other options: nobody on shift is free, Dr. Kim is on a stroke
   video and we never interrupt that, and nobody scheduled starts within the hour. So: a split shift."
3. *Scroll to the eligibility table.* "Every off-duty doctor, every rule. Dr. Okafor is the only one who qualifies.
   Dr. Alvarez is not approved at these hospitals, Dr. Cho's offer would overlap her 7 PM shift, Dr. Menon would go over
   60 hours. If nobody qualified, Synapp would escalate, never bend a rule."
4. *Run the flood guard simulation.* "A new doctor starts with zero consults, so routing would send her every call at
   every hospital. The flood guard keeps her on the hospitals that need help, and here is what happens to everyone
   else's calls."
5. *Close the drawer, click Propose to clinical lead.* Switch to **Clinical lead**. "The clinical lead sees a two-line
   summary and approves in one step." Click **Approve and send**.
6. Switch to **Doctor phone**. "Dr. Okafor gets hospitals, hours, start time, and why her. No patient details. Ten
   minutes to answer." Click **Accept**. "Confirmed, with a calendar invite for Outlook."
7. Switch to **Coordinator**. "Coverage updates: the Northfield gap at 8 PM is closed by the same offer, and Oak Valley
   now says Dr. Okafor joins at 6:35."
8. *The live test.* Click **Add events** and drop `01_new_stroke_suncrest.csv`. "This is what the judges will do: a new
   file, appended to the day, no restart. Synapp drops duplicates, places late records, and tells us right away: a new
   stroke consult at Suncrest, nobody free, here is the fix."

## 3:30 to 4:15 · Technology

*Screen: `docs/architecture.png`.*

"The engine is pure Python and rebuilds everything from the event log in milliseconds, which is why late records,
duplicates, and new files are never a problem. 83 tests, including one that renames every ID and shifts every time to
prove nothing is hardcoded. On AWS: S3 is the event data lake; a Lambda loads any file dropped into it; App Runner runs
the engine; DynamoDB holds offers and the audit log, with a conditional write so only the first acceptance wins;
EventBridge Scheduler runs the timers; SNS sends offers and alerts; CloudWatch alarms if the feed goes quiet; and
Bedrock writes the clinical lead's summary. All of it is infrastructure as code with the AWS CDK."

*Optional if deployed:* show `aws s3 cp` of a practice file into `raw/` and the dashboard updating.

## 4:15 to 5:00 · Feasibility

*Screen: Insights tab, then the audit log.*

"Synapp is built to be deployable. It stores no patient information. A person approves every message to an off-duty
doctor, and every decision is in the audit log. It models Sevaro's routing instead of replacing it, so it plugs into
Synapse; in production the events would stream through Amazon Kinesis into the same engine. And looking back, the
replay shows Synapp would have flagged the Suncrest surge at 5:55 PM, 25 minutes before anyone had to scramble. Synapp:
from seeing the gap to a doctor covering it."
