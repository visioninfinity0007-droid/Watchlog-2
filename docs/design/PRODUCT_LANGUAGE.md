# WatchLog — Product Language (canonical vocabulary)

One word per concept, matching the actual portal. Marketing copy, portal UI, and docs use the
same nouns so the website and the product read as one system.

| Term | Definition | Do NOT say |
|---|---|---|
| **Site** | A physical location with one recorder and its cameras. Billing + reporting unit. | premises (as a product noun), location-id |
| **Recorder** | The customer's existing NVR/DVR. Use "recorder" in prose; "NVR/DVR" only when technically necessary. | box, device, machine, unit (loosely) |
| **Camera** | A single channel on the recorder. | cam, feed |
| **WatchLog Agent** | The Windows program on the site PC that reads the recorder and syncs outward. | client, bot, service (loosely), "the app" |
| **Site PC** | The always-on Windows machine on the recorder's LAN that runs the Agent. | server, gateway |
| **Event** | A raw motion/analytics record from the recorder, before filtering. | alert (for raw), trigger |
| **Incident** | A validated event that passed on-site AI filtering (person / car / motorcycle) — worth reviewing. | alert, alarm, detection (loosely) |
| **Site Health** | Camera-last-seen, offline cameras, recorder faults, agent-reporting status. | uptime, monitoring |
| **Daily Report** | The once-a-day summary delivered to recipients + kept in portal history. | digest, alert email |
| **Recipient** | A person + channel who receives a site's Daily Report. | subscriber, contact |
| **Channel** | Delivery method for a report: WhatsApp, email, or both. | integration |
| **Trial** | 14-day free period; reporting enabled while valid. | free tier, freemium |
| **Plan** | Starter / Growth / Enterprise. Governs cameras, retention, sites. | tier (in UI), package |
| **Entitlement** | Whether reporting is currently enabled (trial valid / active / grace vs expired). | subscription state (in prose) |

## Event vs Incident (load-bearing distinction)

- The recorder produces **events** (motion, analytics). Many are noise (rain, headlights, IR lamp).
- On-site AI keeps only person / car / motorcycle → those become **incidents**.
- Only **incidents** (with one still) sync to WatchLog. Say "event → incident", never blur them.

## Scope guardrails (never overstate)

- Detection scope is **person, car, motorcycle** only. Do NOT imply general object detection,
  face recognition, licence-plate reading, behaviour analytics, or live monitoring/response.
- WatchLog **observes and reports**; it does not prevent incidents, watch live, or dispatch.
- It works **alongside** guards, supervisors, control rooms and site managers — never "instead of".
