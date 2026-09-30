-- 0142_private_vision_worker_throughput.sql
-- Keep the visual-review worker active now that a local/private vision provider is configured.
-- The worker still resolves provider privacy before claiming work and the queue claim itself enforces
-- each site's external-image-egress policy. This change only raises the bounded batch from 1 to 4,
-- which is the Edge Function's existing maximum, so a completed service day can be reviewed promptly.

do $$
begin
  if exists (select 1 from cron.job where jobname='watchlog-vision-worker') then
    perform cron.unschedule('watchlog-vision-worker');
  end if;
end $$;

select cron.schedule(
  'watchlog-vision-worker',
  '* * * * *',
  $job$
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name='wl_project_url' limit 1)
      || '/functions/v1/watchlog-vision-worker',
    headers := jsonb_build_object(
      'Content-Type','application/json',
      'x-watchlog-worker-secret',
      (select decrypted_secret from vault.decrypted_secrets where name='wl_vision_worker_cron_secret' limit 1)
    ),
    body := '{"limit":4}'::jsonb,
    timeout_milliseconds := 120000
  ) as request_id;
  $job$
);
