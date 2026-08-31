# Runbook — Accessing logs

Where every log lives and how to read it. Nothing here needs a value printed to chat.

---

## Site agent (on the customer's Windows PC)
- **Main log:** `C:\ProgramData\WatchLog\agent.log` (plus one rollover `agent.log.old` at ~5 MB).
- **Event spool:** `C:\ProgramData\WatchLog\spool.sqlite` (unsent events; drains when the link returns).
- **Local state:** `C:\ProgramData\WatchLog\agent_state.json` (agent id/key — do not paste its contents).
- **Config:** `C:\Program Files\WatchLog\watchlog.ini` (recorder host + credentials — do not paste).
- **Read it:** open in Notepad, or `Get-Content -Tail 200 -Wait "$env:ProgramData\WatchLog\agent.log"`.
- **Is it running?** Task Scheduler → **WatchLog Agent** (SYSTEM, At startup). `Get-ScheduledTask
  -TaskName 'WatchLog Agent' | Get-ScheduledTaskInfo` shows LastRunTime / LastTaskResult.

## Cloud services (Coolify host `161.97.175.15`)
SSH: `ssh -i projects/alkhalid-security-portal/ak_key.pem root@161.97.175.15`.

| Service | Container | Logs |
|---|---|---|
| Portal | `u10fp0bsvkwjmj8kpty5zk40-*` | `docker logs --tail 200 <container>` |
| Push bridge | `ixi45m3km4kp8hrnv0qzh26x-*` | `docker logs --tail 200 <container>` |
| Marketing site | `wordpress-ez7677oub6mo1c2gwukaynuk-*` | `docker logs --tail 200 <container>` |

Find the exact name with `docker ps --format '{{.Names}}' | grep -E 'u10fp0|ixi45m3|ez7677'`.
Or use the Coolify UI (Application → Logs) with the API token in `.env`.

## Supabase (database + auth + API)
- Dashboard → **Logs** (Postgres, PostgREST, Auth/GoTrue). Filter by time or status.
- **Report delivery history** (the customer-facing audit of what was sent):
  ```sql
  select report_date, channel, destination, status, provider_id, error, sent_at
  from report_deliveries order by sent_at desc limit 50;
  ```
- **Agent liveness / fleet:**
  ```sql
  select * from v_agent_fleet order by last_seen_at desc;   -- status: online/stale/offline
  ```
- **pg_cron** (nightly retention) history:
  ```sql
  select * from cron.job_run_details order by start_time desc limit 20;
  ```

## Reporting job
- Manual/dry run prints channel readiness + per-site render:
  `python prototype/reporter/daily_report.py` (add `--send` only when delivering).
- Scheduled runs (n8n): n8n UI → the **WatchLog daily report** workflow → Executions.

## CI / build
- GitHub → Actions → the **CI** workflow → the run for your commit (backend + portal jobs).

---

**Privacy:** logs may reference camera names and recipient destinations. Do not copy raw log contents
(credentials, tokens, phone numbers) into chat, tickets, or commits. Quote line references, not values.
