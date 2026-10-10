# Assumptions

Every interpretation and default Relay uses, one line each with the reason. Numbers live in `config.yaml`.

## Time and ordering

- **"Now" for the kit is 6:20 PM Eastern on the scenario date.** The brief says so; the scenario date is the Eastern date of the earliest event.
- **After a file is added, "now" becomes the latest `ingested_at` we have.** That is the newest information we have actually received.
- **An event counts at time t only if it has happened and arrived by t.** This lets the replay show what we really knew at any moment.
- **Events are ordered by `event_time`, then `ingested_at`, then `event_id`.** The brief warns that arrival order gives the wrong state.
- **Minutes are floored.** This is how the reference snapshot counts them (for example 10 min 55 s shows as 10).
- **A shift whose end time is at or before its start crosses midnight.** On-shift times have no date.
- **The 22:00 to 07:00 coverage block wraps midnight.** It is read as two pieces of the same block.

## Duplicates, late events, and re-calls

- **Same `event_id` twice: keep the copy that arrived first.** EVT-00529 appears twice with only the arrival time different.
- **Same `event_id` with different content: keep the first and report a conflict.** Never guess which one is right.
- **Same content under a different `event_id`: keep the first and report it.** The brief says duplicates can arrive with a different ID.
- **An event is "late" if it arrived more than 60 seconds after it happened.** Normal lag in the kit is under 5 seconds; EVT-01161 is 405 seconds.
- **A second `consult_received` for an existing consult is a re-call.** Same patient, no new demand, clock not restarted (handbook issue 7).
- **A new consult ID is linked to an old one only if its description names that consult at the same hospital.** The handbook says never merge on a guess.

## Consult lifecycle

- **Stages only move forward.** A later call, like PHY-003 checking on CON-2102's imaging, must not reset the stage or its clock.
- **Provider Contacted starts at the first call after assignment.** CON-2104 enters it at its `incoming_call_started` time (18:14:00).
- **A ringing call does not change the consult stage.** CON-2109 is still Received while its call rings.
- **A call end marked "phone-only" moves the consult to Note Pending and starts the note clock.** CON-2111 has no video.
- **If a note starts with no video and no phone-only marker, the note clock starts at the last call end.** "From end of video or call."
- **Reassignment does not restart the stage clock.** The patient has been waiting the whole time.
- **Consult type comes from the `consult_received` description.** If unknown, the consult is treated as Stroke and flagged, because that is the safer error.
- **If a consult's received event is missing, its clock starts at the first event we saw for it.** It is flagged.

## Physician activity and availability

- **`consults_this_shift` = distinct consults assigned to the physician during this shift.** Matches the snapshot for all 8 physicians.
- **`calls_this_shift` = incoming plus outgoing calls started during this shift.** Ringing calls do not count. Matches the snapshot.
- **`open_consults` = consults currently assigned to the physician and not signed off.** Matches the snapshot.
- **Derived state order: video, then call, then Temporarily Unavailable, then camera without Synapse, then In Meeting, then writing a note, then Available.** This reproduces every snapshot label.
- **A ringing phone does not change the derived state, but it blocks routing.** PHY-007 shows Available while CON-2109 rings to her.
- **Self-reported status is Available until a status event says otherwise.** Every physician sets Available at shift start in the kit.
- **While the desktop agent is offline, camera state is unknown.** The brief says so.
- **A camera event after the agent went offline means the agent is reporting again.** There is no "heartbeat restored" event type in the brief.
- **An unanswered ring older than 3 minutes counts as a missed call, not a ringing phone.** Real phones roll over; otherwise one ring would block a doctor forever.
- **Writing a note is not busy for routing, but the doctor is marked interruptible.** The brief's busy rules only name calls and video.

## Shifts and physicians

- **A roster physician's `next_shift_start` is a scheduled shift of 12 hours.** Night shift length is not given.
- **A shift starting at or after 19:00 is a night shift.** Used for the routing low-volume threshold (4 or fewer on nights).
- **A `shift_started` event within 60 minutes before a scheduled shift confirms that shift.** Otherwise it opens a new "joined" shift and is flagged.

## Data quality

- **Rows with a missing ID, a bad timestamp, or an unknown event type are skipped and reported.** Never crash on bad rows.
- **Events with an unknown physician or hospital are kept and reported.** The consult still exists even if a lookup fails.
- **We also accept `agent_heartbeat_restored`, `shift_ended`, `consult_reclassified`, and `consult_cancelled`.** New data may use them; anything else unknown is skipped.
- **Baseline rows are matched to the scenario day type (weekday for Tuesday 17 March 2026).** Missing hours are reported and skipped for surge checks.
- **A hospital with no coverage requirement row is assumed to need 1 physician.** Reported as a warning.

## Surge and time standards

- **Surge = distinct consults first received in the trailing 60 minutes, strictly above the p90 for the hour containing "now".** Each hospital is compared with its own normal level.
- **Re-calls never count as demand.** They do not create a consult.
- **In projections, a surge is assumed to last until the end of its hour plus 60 minutes.** Suncrest's 6 PM surge is planned through 8:00 PM.
- **A missing baseline hour means no surge check for that hour.** It is reported in data health.
- **Status uses exact elapsed time; displayed minutes are floored.** 10 min 55 s is past a 10-minute limit but shows as 10.
- **The note clock runs from the end of the video or call, across Note Pending and Documentation together.** As the brief says.
- **General neurology imaging steps have no standard.** The brief lists those standards for stroke only.
- **"Received, not triaged" shows as information after 5 minutes.** There is no standard for it.

## Routing model

- **The kit's real assignments match the brief's routing rules only about 55% of the time.** The synthetic data does not follow them strictly, so we model the rules as written and do not tune them to the data.
- **A doctor with a consult assigned but not yet contacted is treated as having a phone about to ring.** This reproduces all 4 recorded routing failures; without it, routing would have sent CON-2105 to Dr. Williams at 6:09 PM.
- **The 2-minute cooldown applies to phone calls only.** The brief says "ended a call".
- **Rule 2 also removes Temporarily Unavailable doctors and "camera on, no Synapse session" doctors.** Both are not free by the availability rules.
- **No answered call yet counts as least recent for ties.** Rule 4 says ties go to whoever answered least recently.

## Coverage

- **Staffed = on shift, credentialed, and not Temporarily Unavailable. A status is assumed to last until it changes.** We do not guess when someone comes back.
- **Scheduled roster shifts count from their start.** Dr. Cho joins at 7:00 PM and Dr. Haddad at 8:00 PM.
- **A "nobody free now" gap exists when a hospital has a waiting consult and no credentialed doctor that routing could use.** Waiting means unassigned and not ringing, or assigned to an unavailable doctor.
- **A "nobody free now" gap is planned for at least 60 minutes, or until the surge is expected to end.** We cannot know when a busy doctor will really be free.
- **The projection runs every 15 minutes to the latest of now + 90 minutes, 9:30 PM, and now + 3 hours, and also checks every exact shift change.** It covers the 7, 8, and 9 PM shift changes.

## Ranking and plans

- **Order: stroke first, then critical, warning, projected, info; then nobody working on it, a delayed doctor, a hospital-side delay; then re-calls; then longer wait.** Fixed and explainable.
- **Harbor (CON-2110) ranks above Oak Valley (CON-2105).** Both are critical stroke consults with nobody working on them; Harbor's patient has waited 16 minutes and Oak Valley's 12. A re-call at Oak Valley moves it to the top.
- **A re-call turns a warning into critical and moves the consult up.** The handbook says it should move up the list.
- **Plans are made in rank order.** The most urgent issue gets first claim on a scarce doctor.
- **Never interrupt a stroke call or video.** The mentor said someone on an emergency case is priority 1.
- **"Wait for the assigned doctor" is offered only when they should be free within 10 minutes.** Otherwise it is not a plan.
- **Free-in estimate: the doctor's own median call or video length if they have 3 or more today, otherwise the team median. Over twice the typical length is a long case with no estimate.**
- **Asking a scheduled doctor to start early is limited to 60 minutes before their shift.** More than that is an extra shift and goes through the split-shift rules. Rest and weekly hours are still checked.
- **A critical stroke consult whose fix is not immediate is also escalated to the clinical lead.**
- **Imaging pending past its limit is a hospital-side delay; the fix is to call the hospital, not to change doctors.**
- **Imaging back, video not started, and the assigned doctor free means a possible technology problem at the hospital.**

## Split-shift offers

- **Offer start = the later of the gap start and now + time to get online. The doctor is expected online at the start.**
- **Offer end covers the gap, then stretches to later gaps at hospitals the doctor is credentialed at (combined coverage).** Okafor's offer reaches 10:00 PM so it also covers Northfield's 8:00 PM gap.
- **Offers last at least 2 hours after time to get online and at most 4 hours, trimmed to fit the 60-hour rule and the next shift.**
- **Value rule: offer length minus time to get online must be at least 120 minutes.** The handbook's example confirms this reading.
- **The weekly-hours rule fails if even the shortest workable offer would pass 60 hours.** Exactly 60 passes ("do not exceed").
- **Rest is measured at the offered start.** Exactly 10 hours passes ("at least").
- **Being already scheduled to work during the offer counts as an overlap.**
- **The top recommendation targets the "nobody free now" gaps; each later projected gap gets its own eligibility table.**
- **Eligible doctors are ranked by gap hospitals covered, then time to get online, then fewer hours this week, fewer offers this week, fewer recent declines.** Never cost, as Sevaro asked.
- **Offers this week start at 0.** The kit has no offer history; Relay keeps its own.

## Offers and approvals

- **The clinical lead approves a ranked plan once; Relay then offers one doctor at a time.** It moves down the list on a decline, an expiry, or a no-show. "Top two at once" is available for urgent stroke gaps under the same approval.
- **First acceptance wins, by server order.** A DynamoDB conditional write in the cloud, a lock locally. Later acceptances get "This shift is already filled. Thank you." and are logged.
- **An accepted doctor is counted from the later of the offer start and acceptance time plus their time to get online.**
- **No-show: not online within 10 minutes of that time (no `shift_started` event and no "I'm online" tap).** Relay moves to the next doctor on the list.
- **No decision on a plan within 5 minutes sends it to a backup clinical lead.** An internal alert, allowed automatically.
- **A plan is cancelled, and its open offers retracted, when none of its gaps remain open.**
- **Offer history starts at zero for the kit and is kept by Relay.** It drives the 3-per-week and one-open-offer rules.
- **A doctor who accepted an offer is not offered another shift until that one ends.** They already count as coverage.
- **"Start early" requests go through the same approval and offer flow.** Any message to a doctor outside their shift needs approval.
- **A critical issue the coordinator has not acknowledged within 3 minutes alerts the clinical lead automatically.** Internal and low risk.
- **Offer reasons are limited to "coverage gap now", "projected coverage gap", and "demand surge".** The guard rejects consult IDs, event descriptions, and clinical words.
- **Coordinator reassignments are recorded and audited, not pushed into Synapse.** Relay models routing; a `physician_assigned` event confirms the change.

## Clock

- **"Now" follows the data: 6:20 PM for the kit, or the latest arrival time after an upload.**
- **Demo controls can set, advance, play, or pause the clock.** New data never moves a later demo clock backward.

## Flood guard and its simulation

- **A joiner (split shift, scheduled night start, or unscheduled shift start) is counted at the team median of consults when below it.** The brief's routing rules otherwise send them nearly every call.
- **A split-shift joiner only takes calls at the hospitals in their offer.** Scheduled night doctors are not limited, only seeded.
- **Simulation: 2 hours, 50 runs, fixed seed 17.** Arrivals follow each hospital's baseline mean for the hour, or the current trailing count while a surge lasts.
- **A doctor is busy about 17 minutes per consult in the simulation.** That is today's average time on calls and video per consult; imaging waits do not count.
- **Calls nobody can take retry every 5 minutes, as routing rule 5 says.** Busy doctors free up at their estimate (10 minutes if unknown).
- **Honest finding: tonight is so busy that the guard moves only a few calls.** Its visible effect is keeping the new doctor off hospitals with no gap.

## Cloud

- **App Runner runs one instance because the engine keeps state in memory.** Scaling out would rebuild state per instance from S3 and DynamoDB.
- **A file uploaded through the page is stored in S3 and also triggers the ingest Lambda.** The second reload is harmless because duplicates are dropped.
- **Raw event files are never deleted; a demo reset moves them to `archive/`.**

## Standouts

- **The scoreboard reruns the engine every 5 minutes from the first hour of the day, using only records that had arrived by then.** It reports when a problem was first flagged, never patient outcomes.
- **Scoreboard headlines use the current episode.** For example, when today's Suncrest surge began, not an earlier surge that ended; the recommendation time is when the current top doctor first became the recommendation.
- **Credentialing impact counts three kinds of problems a doctor could have covered if credentialed at that hospital.** A failed routing attempt while they were free; a "nobody free now" gap while they are free; a projected gap of one doctor while they are on shift.
- **Handoff cards cover every open consult of a doctor within 60 minutes of shift end.** Notes are suggested to be finished by the same doctor; other stages go to the least-loaded credentialed doctor on shift after they leave.
- **The clinical lead summary is two sentences from structured, non-patient fields.** Amazon Bedrock writes it when a model is configured; otherwise a template does. The decision is always made by the rules.
- **"Value of acting" compares the simulation with and without the recommended offer.** It is an estimate of waits and unanswered calls, never a comparison of doctors by cost. The note on 4% better odds per 15 minutes is labeled as an illustrative research average.
- **Offer audio uses ElevenLabs when a key is configured and the browser voice otherwise.** Only the guarded offer text is ever spoken.
