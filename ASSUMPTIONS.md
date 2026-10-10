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
