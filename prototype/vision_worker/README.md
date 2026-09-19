# WatchLog private vision worker

This Coolify service analyzes the snapshots already authorized and persisted by WatchLog.

## What it does

1. Claims bounded snapshot jobs through service-role-only RPCs.
2. Sends the JPEG to a private Ollama multimodal model on the same Coolify network.
3. Stores structured, non-identifying visual findings in `snapshot_visual_reviews`.
4. Adds the finding to the scoped `ai_evidence` workspace.
5. When every snapshot for a site/day is reviewed, creates a concise owner-facing visual day summary.

Raw CCTV bytes are not stored in Git and are not sent to third-party AI providers.

## Coolify

Create a Docker Compose app using this directory and `docker-compose.coolify.yml`.

Required secrets:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Optional:
- `VISION_MODEL=gemma3:4b`
- `VISION_AUTO_PULL=true`
- `VISION_BATCH_SIZE=4`
- `VISION_POLL_SECONDS=5`

The default model is Gemma 3 4B because Ollama supports it as a multimodal image model. The model is pulled into the persistent `watchlog_ollama` volume on first start.

The service has no public endpoint requirement. Its health endpoint is only for the container/Coolify health check.
