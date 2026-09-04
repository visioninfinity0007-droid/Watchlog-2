# WatchLog read-only deployment smoke

Use this after a deployment from a machine/network that can reach the WatchLog public URLs.

It issues **GET requests only** and carries no Supabase key, recorder token, report-runner token, billing secret, or customer credential.

## Run

```bash
python tools/watchlog_deployment_smoke.py \
  --portal 'https://<portal-host>' \
  --marketing 'https://<marketing-host>' \
  --push 'https://<push-host>' \
  --reporter 'https://<report-host>' \
  --billing 'https://<billing-host>' \
  --output watchlog-deployment-smoke.json
```

The same bases can be supplied with:

```text
WATCHLOG_PORTAL_URL
WATCHLOG_MARKETING_URL
WATCHLOG_PUSH_URL
WATCHLOG_REPORT_URL
WATCHLOG_BILLING_URL
```

## Checks

The tool verifies:

- portal `/login/` returns a 2xx WatchLog page;
- marketing homepage returns a 2xx WatchLog page;
- marketing `/sitemap.xml` returns a 2xx sitemap-like body;
- marketing `/robots.txt` returns a 2xx body referencing a sitemap;
- push bridge root returns the WatchLog push-bridge health text;
- report-runner root returns JSON with `service=watchlog-report-runner` and `ok=true`;
- billing `/health` returns JSON with `service=watchlog-billing` and `ok=true`.

## What a pass proves

A full pass proves that the expected unauthenticated HTTP surfaces are reachable and match the repository's basic response contracts from the network where the command was executed.

## What it does not prove

It does **not** prove:

- the exact deployed Git commit/image;
- authenticated portal RPC behavior;
- tenant/platform authorization;
- report delivery;
- billing mutation/webhooks;
- recorder push ingestion;
- production database migration parity;
- Windows installer download/lifecycle;
- field recorder/camera behavior.

Those remain separate production gates.

## Current ChatGPT runtime result — 4 September 2026

The temporary sslip.io WatchLog hostnames could not be resolved from this execution runtime. A direct-IP test using the known server IP plus the correct TLS hostname/SNI also could not connect to port 443.

Record this only as **verification unavailable from this runtime**. It is not evidence that production is down.

Run this tool from the Coolify host, an operator workstation, or another network that can reach the deployed endpoints and retain the JSON output with the deployment evidence.
