# Method: Analysing an Incident Clip

**Customer status:** Serious incidents are raised by site rules. Incident clips and clip review are being introduced and are not yet available at every site.

**Status:**
- PARTLY LIVE:
  - incident rules raise incidents;
  - clip download from the recorder is being fixed in the site agent (Build 100 adds a second Dahua
    download method);
  - FFmpeg is bundled on the site agent.
- PLANNED: automatic clip analysis and instant owner alerts. Owner alert delivery has not yet
  delivered a message on any channel.

Internal method. Customer wording is in the last section.

## Procedure

1. **Trigger.** A serious-incident rule fires, for example a person lying down, an object gone from
   its confirmed zone, restricted-area access after hours, or a fire check.
   - Gate rules by hours and dwell so owners are not trained to ignore alerts: 53 unacknowledged
     critical alerts at one site showed the cost.
2. **Fetch the clip** for the incident window, about 30 s before to 60 s after, from the recorder via
   the site agent. Clips are kept 72 hours (`core/retention.yaml`).
3. **Build the timeline** from about 2 observations per second. For each, detect people and objects,
   track them across time, and check posture (lying down).
   - The result is a timeline, e.g. "0:03 person enters till area, 0:11 laptop leaves with that
     person, 0:14 exits".
   - Measured detector cost: ~0.2 s per observation, so about 15 s for a 30 s clip.
4. **Alert the owner immediately** with the clip and the timeline. Do not wait for an AI verdict.
5. **Check 3-5 key moments** where something changed:
   - fire/smoke: local check;
   - fights, weapons or anything unclear: human review, or a cloud model only for sites that allow
     external processing.
   - At security sites, guards carry weapons normally, so a weapon rule must be site-specific.
6. **Send the verdict as a follow-up** to the same alert. Record everything in the incident record.

## Truth rules

- The timeline is observed fact. Intent, wrongdoing and identity are never inferred.
- A clip that could not be retrieved is "footage not available", never "nothing happened".
- Detection is not confirmation. Say "appears to" until a human or a validated check confirms.

## Customer-facing claims

- "At 9:14 PM someone appeared to fall near the kitchen door and was still down 20 seconds later. The
  clip is attached."
- "The camera shows the laptop leaving the desk at 9:21 PM with the person who was at the desk."
- Never mention how the clip was fetched, processed or analysed.
