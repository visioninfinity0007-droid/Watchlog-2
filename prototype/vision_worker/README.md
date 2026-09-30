# WatchLog private vision worker

This Coolify service analyzes the snapshots already authorized and persisted by WatchLog.

## What it does

1. Claims bounded snapshot jobs through service-role-only RPCs.
2. Mirrors each JPEG into a private MinIO bucket on the Coolify network.
3. Sends the private image to a local Ollama multimodal model on that same network.
4. Stores structured, non-identifying visual findings in `snapshot_visual_reviews`.
5. Adds the finding to the scoped `ai_evidence` workspace.
6. When every snapshot for a site/day is reviewed, creates a concise owner-facing visual day summary.

Raw CCTV bytes are never stored in Git and are not sent to third-party AI providers. MinIO has no public port in the compose stack. Supabase remains the ingestion fallback during rollout; the worker records an opaque private-media key after each successful mirror.

## Coolify

Create a Docker Compose app using this directory and `docker-compose.coolify.yml`.

Required secrets:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `WATCHLOG_MEDIA_ACCESS_KEY` — generate a long random value in Coolify
- `WATCHLOG_MEDIA_SECRET_KEY` — generate a long random value in Coolify

Optional:
- `VISION_MODEL=gemma3:4b`
- `VISION_AUTO_PULL=true`
- `VISION_BATCH_SIZE=4`
- `VISION_POLL_SECONDS=5`

The default model is Gemma 3 4B because Ollama supports it as a multimodal image model. The model is pulled into the persistent `watchlog_ollama` volume on first start.

The service has no public endpoint requirement. Its health endpoint is only for the container/Coolify health check.


## Media retention

The worker configures a bucket lifecycle automatically. Default private-media retention is 30 days and can be changed with `WATCHLOG_MEDIA_RETENTION_DAYS`. Structured findings remain governed by WatchLog's existing evidence/report retention.

## Security boundary

Do not expose MinIO ports 9000/9001 publicly. The worker and MinIO communicate only over the internal `watchlog_vision` Docker network. The bucket name/key may be stored internally for provenance; customer-facing Watch AI must not reveal object-store paths or implementation details.
