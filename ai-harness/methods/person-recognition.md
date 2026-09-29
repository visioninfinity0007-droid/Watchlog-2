# Method: Recognising People (staff profiles and faces)

**Customer status:** Face recognition is not available at any site yet.

**Status: NOT LIVE.** No site can do face recognition today. Until every condition below is met, no
answer, report or alert may claim that a person was recognised.

Internal method. Customer wording is in the last section.

## Why it is not live (measured 28 Sep 2026)

- The face engine works: 139 ms per image on the WatchLog server.
- It found **0 faces in all 13 current images**, even at 2x upscaling.
- Current stills show faces at 7-46 px high. Recognition needs about 80-112 px.
- The cameras look down from above at low resolution.
- The recorders do not provide face analytics:
  - Al-Khalid's DH-XVR1B08-I is field-verified as human/vehicle only.
  - The DS-7608NI-Q1 (Chai Wala, HASCO) lists human/vehicle analysis on 4 channels only; face depends
    on the camera.

## Conditions before recognition may go live (all required)

1. **Consent and scope.**
   - Written client consent.
   - Only people who agreed to be enrolled, normally staff.
   - Never customers or the public.
   - Each profile can be withdrawn and deleted on request.
2. **Full-resolution capture.** The Dahua full-resolution still fix must be live on the site agent.
3. **A face-height camera** where people naturally face it: entrance, till or reception, with
   adequate light.
4. **Profiles live only in the WatchLog database** with retention and access control.
   - Never in Git, the harness, chat history or reports.
   - Face data is biometric: store and process it only under the site's egress policy.
5. **Validation.** Measured match accuracy on the site's own camera against a human-checked sample,
   before any claim is shown to a customer.

## Procedure once live

1. Enrol 3-5 consented reference photos per person; store only the face data needed for matching.
2. Match only faces large and sharp enough to pass the quality gate. Below it, the result is
   "unidentified", never a guess.
3. Report a match with its certainty in plain words. An unmatched face is "an unidentified person".
4. Never infer identity from clothing, gait, role, location or timing.
   Behaviour-based labels such as "appears to be regular staff" stay labelled as estimates.
5. Attendance or time-on-site claims require an enrolled person plus a face-height camera at the
   entry point, and they still carry coverage caveats.

## Customer-facing claims

- Today: "an unidentified person", "someone who appears to be regular staff (estimate)". Never a name.
- Once live, for consented enrolled staff only: "Recognised as [enrolled name] at the entrance at
  9:02 AM."
- Never recognise or name customers or visitors.
