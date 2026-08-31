# Runbook — NVR / recorder troubleshooting

The agent runs on a Windows PC on the **same LAN** as the recorder and connects outbound only. Most
issues are on-site (network, credentials, firmware quirks). Work top-down.

Diagnostics live in the agent: `watchlog-agent.exe --probe` prints a checklist against the configured
recorder; `--setup` re-runs discovery + connection test.

---

## 1. Agent not checking in (no heartbeat)
- **Portal shows offline / never seen.** On the site PC: is the task running?
  `Get-ScheduledTask -TaskName 'WatchLog Agent' | Get-ScheduledTaskInfo` (LastRunTime, LastTaskResult).
- Read `C:\ProgramData\WatchLog\agent.log` (see `LOG_ACCESS.md`). Look for the last line and any
  traceback. The runner restarts the exe 15 s after any exit; a tight crash loop means a config or
  driver error, not a transient one.
- **Known real-hardware issue (SM-HP, DH-XVR1B08-I):** the agent stopped after ~3 minutes and synced
  0 cameras. First action is always to pull the `agent.log` from that machine — the stop reason is in
  it. Do not guess.

## 2. Recorder not found during setup
- Wrong IP or the PC is on a different subnet/VLAN than the recorder. Confirm with `ping <nvr-ip>` and
  that the NVR's web UI opens from that PC.
- Vendor API disabled: Hikvision needs **ISAPI** enabled; Dahua needs **CGI**; ONVIF needs the ONVIF
  service on and an ONVIF user. Enable in the recorder's web UI.
- Non-standard port: set it in `watchlog.ini`.
- **Xiongmai/Hisilicon no-name DVRs (port 34567)** are not supported and ONVIF is often broken on
  them — flagged by discovery, not a bug.

## 3. Authentication fails
- `--probe` reports a 401. Re-check the recorder admin username/password in `watchlog.ini`.
- Some Dahua/Hikvision units lock out after failed logins — wait or clear the lockout in the NVR UI.
- Digest vs basic auth is handled automatically; no action needed.

## 4. Events arrive but no cameras / wrong camera on snapshots
- **0 cameras synced** on a real unit usually means `list_channels` did not match that firmware's
  channel layout. Capture `--probe` output and the model/firmware; the Dahua/Hikvision channel
  enumeration has model-specific shapes.
- **Hikvision snapshot channel numbering:** the streaming API uses `<channel><stream>` (channel 2 is
  `201`). A wrong number returns a *different camera's* picture — verify the snapshot matches the
  camera before trusting it.

## 5. Events stop after an outage
- **Internet outage:** expected-safe. Events buffer in `spool.sqlite` and drain when the link returns
  (at-least-once; the server dedupes). No action unless the spool hits its 200k cap (logged loudly).
- **Recorder/agent downtime:** the push-based event streams do **not** replay history after a
  reconnect — alarms that fired while the agent was down are lost at the source (recorders don't
  store-and-forward). Keep the site PC on and on mains power; the installer disables sleep/hibernate,
  but enable "power on after power loss" in the PC BIOS (the installer cannot).

## 6. False alarms not being filtered
- The on-site AI filter only runs if `yolov8n.onnx` is present next to the agent and the ONNX runtime
  is packaged. If the model is absent the agent **fails open** and reports everything (by design).
  Confirm the release you shipped is the AI build (see `AGENT_RELEASE.md`) and the model file exists.

## 7. Useful queries (ops)
```sql
-- what has this site's agent reported lately?
select event_type, count(*), max(device_ts)
from events where site_id = '<site-uuid>'
group by 1 order by 3 desc;

-- fleet health
select tenant, site, hostname, status, last_seen_at, event_count from v_agent_fleet;
```

**Escalation:** if the recorder is reachable, authenticated, and `--probe` succeeds but cameras/events
are wrong, capture `--probe` output + model + firmware and file it against the driver — it is almost
always a firmware-shape mismatch to fix in `prototype/agent/drivers/`.
